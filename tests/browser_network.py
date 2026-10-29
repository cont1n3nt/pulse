"""Run network recovery and basket regressions: python tests/browser_network.py -v.

Uses real local HTTP streams; Playwright's clock advances frontend deadlines only.
"""
import asyncio
import json
import time
import unittest

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
import browser_purchases


class NetworkRecoveryTests(browser_purchases.BasketStatusTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        @cls.app.api_route('/qa/json/{mode}', methods=['GET', 'POST'])
        async def json_response(mode: str):
            if mode == 'headers':
                await asyncio.sleep(2)
                return {'ok': True}
            if mode == 'error':
                return JSONResponse({'detail': 'Expected rejection'}, status_code=409)
            async def chunks():
                yield '{"ok":'
                await asyncio.sleep(2 if mode == 'body' else .05)
                yield 'true}'
            return StreamingResponse(chunks(), media_type='application/json')

        @cls.app.post('/qa/stream/{mode}')
        async def stream(mode: str):
            if mode == 'headers':
                await asyncio.sleep(2)
            async def chunks():
                def event(data):
                    return 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
                yield event({'type': 'token', 'text': 'Часть ответа'})
                if mode == 'truncated':
                    return
                if mode == 'error':
                    yield event({'type': 'error', 'detail': 'Expected agent failure'})
                    return
                if mode in ('stall', 'headers'):
                    await asyncio.sleep(3)
                if mode == 'progress':
                    await asyncio.sleep(.3)
                    yield event({'type': 'token', 'text': ' — продолжение'})
                    await asyncio.sleep(.3)
                yield event({'type': 'done'})
                if mode == 'done-open':
                    await asyncio.sleep(3)
            return StreamingResponse(chunks(), media_type='text/event-stream')

        @cls.app.post('/qa/pay-unknown/{chat_id}')
        async def pay_unknown(chat_id: str, request: Request):
            # The real mutation succeeds, but its JSON response is interrupted.
            from backend.endpoints import CartAction, mutate_cart
            payload = CartAction(**await request.json())
            result = await asyncio.to_thread(mutate_cart, request, chat_id, payload, True)
            async def chunks():
                yield '{"order":'
                await asyncio.sleep(3)
                yield json.dumps(result['order']) + '}'
            return StreamingResponse(chunks(), media_type='application/json')

    def setUp(self):
        super().setUp()
        self.page.clock.install()
        self.page.evaluate("""() => {
            window.qaHeaders = false;
            const originalFetch = window.fetch;
            window.fetch = async (...args) => {
                const response = await originalFetch(...args);
                if (String(args[0]).includes('/qa/')) window.qaHeaders = true;
                return response;
            };
        }""")

    def start_api(self, mode, method='GET'):
        self.page.evaluate("""({mode, method}) => {
            window.qaResult = null;
            api('/qa/json/' + mode, {method}).then(
                data => window.qaResult = {data},
                error => window.qaResult = {message:error.message, code:error.code,
                    unknown:error.resultUnknown, status:error.status});
        }""", {'mode': mode, 'method': method})

    def start_stream(self, mode):
        self.page.route('**/api/chat', lambda route: route.continue_(
            url=self.url + '/qa/stream/' + mode))
        self.page.locator('#chat-input').fill('Тест сетевого ответа')
        self.page.locator('#chat-send').click()
        if mode != 'headers':
            self.page.wait_for_function('document.querySelector("#chat-messages").textContent.includes("Часть ответа")')

    def assert_unlocked(self):
        self.page.wait_for_function('!chatBusy && !chatLocked()')
        self.assertTrue(self.page.locator('#chat-input').is_enabled())
        self.assertTrue(self.page.locator('#new-chat').is_enabled())
        self.assertTrue(self.page.locator('#chat-stop').is_hidden())
        self.assertTrue(self.page.locator('#chat-send').is_visible())

    def test_api_deadline_includes_json_body(self):
        self.start_api('body')
        self.page.wait_for_function('window.qaHeaders')
        self.page.clock.fast_forward(20001)
        self.page.wait_for_function('window.qaResult !== null')
        result = self.page.evaluate('window.qaResult')
        self.assertEqual(result['code'], 'REQUEST_TIMEOUT')
        self.assertFalse(result['unknown'])

    def test_api_deadline_before_headers(self):
        self.start_api('headers')
        self.page.clock.fast_forward(20001)
        self.page.wait_for_function('window.qaResult !== null')
        self.assertEqual(self.page.evaluate('window.qaResult.code'), 'REQUEST_TIMEOUT')

    def test_api_success_and_http_rejection(self):
        self.start_api('success')
        self.page.wait_for_function('window.qaResult !== null')
        self.assertTrue(self.page.evaluate('window.qaResult.data.ok'))
        self.start_api('error', 'POST')
        self.page.wait_for_function('window.qaResult !== null')
        self.assertEqual(self.page.evaluate('window.qaResult.status'), 409)
        self.assertFalse(self.page.evaluate('window.qaResult.unknown'))
        self.page.clock.fast_forward(20001)
        self.assertEqual(self.page.evaluate('window.qaResult.status'), 409)

    def test_stream_timeout_preserves_partial_text_and_unlocks(self):
        self.start_stream('stall')
        self.page.clock.fast_forward(60001)
        self.assert_unlocked()
        self.assertIn('Минуту нет связи', self.page.locator('#chat-error').inner_text())
        self.assertIn('Часть ответа', self.page.locator('#chat-messages').inner_text())
        self.assertTrue(self.page.locator('#chat-reload').is_visible())

    def test_stream_timeout_before_headers(self):
        self.start_stream('headers')
        self.page.clock.fast_forward(60001)
        self.assert_unlocked()
        self.assertIn('Минуту нет связи', self.page.locator('#chat-error').inner_text())

    def test_user_stop_and_reload(self):
        self.start_stream('stall')
        self.page.locator('#chat-stop').click()
        self.assert_unlocked()
        self.assertIn('Ожидание остановлено', self.page.locator('#chat-error').inner_text())
        self.assertIn('Часть ответа', self.page.locator('#chat-messages').inner_text())
        self.click_and_wait_refresh('#chat-reload')
        self.assertTrue(self.page.locator('#chat-error').is_hidden())
        self.assertTrue(self.page.locator('#chat-reload').is_hidden())

    def test_user_stop_before_headers_allows_another_chat(self):
        self.start_stream('headers')
        self.page.locator('#chat-stop').click()
        self.assert_unlocked()
        previous = self.page.evaluate('activeChatId')
        self.page.locator('#new-chat').click()
        self.page.wait_for_function('id => activeChatId !== id && !chatBusy', arg=previous)
        self.page.clock.fast_forward(60001)
        self.assertTrue(self.page.locator('#chat-error').is_hidden())
        self.assertTrue(self.page.locator('#chat-reload').is_hidden())

    def test_reload_timeout_keeps_partial_text_and_retry_available(self):
        self.start_stream('stall')
        self.page.locator('#chat-stop').click()
        self.assert_unlocked()
        self.page.route('**/api/chats/*', lambda route: route.continue_(url=self.url + '/qa/json/body'))
        self.page.locator('#chat-reload').click()
        self.page.wait_for_function('chatBusy')
        self.page.clock.fast_forward(20001)
        self.assert_unlocked()
        self.assertTrue(self.page.locator('#chat-reload').is_enabled())
        self.assertIn('Сервер долго', self.page.locator('#chat-error').inner_text())
        self.assertIn('Часть ответа', self.page.locator('#chat-messages').inner_text())

    def test_truncated_stream_is_not_reported_as_success(self):
        self.start_stream('truncated')
        self.assert_unlocked()
        self.assertIn('прервался', self.page.locator('#chat-error').inner_text())

    def test_agent_error_unlocks(self):
        self.start_stream('error')
        self.assert_unlocked()
        self.assertEqual(self.page.locator('#chat-error').inner_text(), 'Expected agent failure')

    def test_done_finishes_without_waiting_for_connection_close(self):
        self.start_stream('done-open')
        self.assert_unlocked()
        self.assertTrue(self.page.locator('#chat-error').is_hidden())
        self.page.clock.fast_forward(60001)
        self.assertTrue(self.page.locator('#chat-error').is_hidden())

    def test_progress_resets_idle_deadline(self):
        self.start_stream('progress')
        self.page.clock.fast_forward(45000)
        self.page.wait_for_function('document.querySelector("#chat-messages").textContent.includes("продолжение")')
        self.page.clock.fast_forward(45000)
        self.assert_unlocked()
        self.assertTrue(self.page.locator('#chat-error').is_hidden())

    def test_old_stream_cannot_change_another_account(self):
        self.start_stream('stall')
        previous = self.page.evaluate('activeUserId')
        self.context.clear_cookies()
        self.page.evaluate("async () => render(await api('/api/auth/guest', {method:'POST'}))")
        self.page.wait_for_function('id => activeUserId !== id && !chatBusy', arg=previous)
        self.assert_unlocked()
        self.page.clock.fast_forward(60001)
        self.assertNotIn('Часть ответа', self.page.locator('#chat-messages').inner_text())
        self.assertTrue(self.page.locator('#chat-error').is_hidden())

    def test_payment_timeout_reconciles_without_repeating_mutation(self):
        chat = self.seed_cart()
        requests = []
        def route_payment(route):
            requests.append(route.request.url)
            route.continue_(url=self.url + '/qa/pay-unknown/' + chat)
        self.page.route('**/api/purchases/*/pay', route_payment)
        self.page.locator('.purchase-card .pay').click()
        self.page.locator('#confirm-accept').click()
        self.page.wait_for_function('purchaseBusy')
        # Wait until the server committed the operation before advancing client time.
        from backend.agent import service
        deadline = time.monotonic() + 5
        while service.get_state(chat).cart and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertFalse(service.get_state(chat).cart)
        self.page.clock.fast_forward(20001)
        self.page.wait_for_function('!purchaseBusy && purchases.orders.length === 1')
        self.assertIn('Проверьте корзину и заказы', self.status())
        self.assertEqual(self.page.locator('.purchase-card').count(), 0)
        self.assertEqual(len(requests), 1)

    def test_chat_refresh_timeout_does_not_hide_confirmed_payment(self):
        self.seed_cart()
        self.page.route('**/api/chats/*', lambda route: route.continue_(url=self.url + '/qa/json/body'))
        self.page.locator('.purchase-card .pay').click()
        self.page.locator('#confirm-accept').click()
        self.page.wait_for_function('document.querySelector("#basket-status").textContent.includes("оформлен и сохранён")')
        self.page.clock.fast_forward(20001)
        self.page.wait_for_function('!purchaseBusy && purchases.orders.length === 1')
        self.assertIn('оформлен и сохранён', self.status())
        self.page.locator('#basket-close').click()
        self.assertTrue(self.page.locator('#chat-reload').is_visible())
        self.assertIn('Операция выполнена', self.page.locator('#chat-error').inner_text())


if __name__ == '__main__':
    # The inherited basket cases also guard the previous fix against API regressions.
    unittest.main()
