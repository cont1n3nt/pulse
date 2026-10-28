"""Browser tests for switching/creating chats while agent is thinking and background reload/cart sync."""
from datetime import date
import threading
import unittest
from unittest.mock import patch

import browser_purchases
from backend.agent import service
from backend.agent.adapters import hotels
from backend.agent.trip import hotel_item


class ChatSwitchingWhileThinkingTests(browser_purchases.BasketStatusTests):
    def setUp(self):
        self.gates: dict[str, threading.Event] = {}
        self.started: dict[str, threading.Event] = {}
        self.stream_actions: dict[str, callable] = {}

        def fake_stream_message(session_id: str, text: str, user_id: str):
            start_ev = self.started.setdefault(session_id, threading.Event())
            gate = self.gates.get(session_id)
            yield {"type": "tool", "name": "plan_trip"}
            yield {"type": "token", "text": f"Ответ для: {text}"}
            start_ev.set()
            if action := self.stream_actions.get(session_id):
                action(session_id, user_id)
            if gate is not None:
                gate.wait(timeout=10)
            yield {"type": "done"}

        self._patcher = patch("backend.endpoints.stream_message", side_effect=fake_stream_message)
        self._patcher.start()
        super().setUp()

    def tearDown(self):
        try:
            for gate in self.gates.values():
                gate.set()
            super().tearDown()
        finally:
            self._patcher.stop()

    def hold_chat(self, chat_id: str) -> threading.Event:
        gate = threading.Event()
        self.gates[chat_id] = gate
        self.started[chat_id] = threading.Event()
        return gate

    def test_new_chat_and_parallel_replies_while_thinking(self):
        chat1 = self.page.evaluate("activeChatId")
        gate1 = self.hold_chat(chat1)

        self.page.locator("#chat-input").fill("Поездка один")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        # Chat 1 input is locked, but creating a new chat and using sidebar is allowed.
        self.assertTrue(self.page.locator("#chat-input").is_disabled())
        self.assertTrue(self.page.locator("#chat-stop").is_visible())
        self.assertTrue(self.page.locator("#new-chat").is_enabled())
        self.page.wait_for_function(
            "document.querySelector('#chat-list .trip small.thinking')?.textContent === 'Агент отвечает…'"
        )

        # Create Chat 2 while Chat 1 is still thinking and get an immediate reply in Chat 2.
        self.page.locator("#new-chat").click()
        self.page.wait_for_function("id => activeChatId && activeChatId !== id && !chatBusy", arg=chat1)
        chat2 = self.page.evaluate("activeChatId")
        self.assertTrue(self.page.locator("#chat-input").is_enabled())
        self.assertTrue(self.page.locator("#chat-stop").is_hidden())

        self.page.locator("#chat-input").fill("Поездка два")
        self.page.locator("#chat-send").click()
        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Ответ для: Поездка два') && !chatLocked()"
        )
        # Chat 1 is still marked as thinking in the sidebar.
        self.assertEqual(self.page.locator("#chat-list .trip small.thinking").count(), 1)

        # Finish Chat 1 in the background and verify sidebar updates and history is saved when opened.
        gate1.set()
        self.page.wait_for_function("document.querySelectorAll('#chat-list .trip small.thinking').length === 0")
        self.assertEqual(self.page.evaluate("activeChatId"), chat2)

        self.page.locator("#chat-list .trip", has_text="Поездка один").click()
        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Ответ для: Поездка один') && !chatLocked()"
        )

    def test_return_while_thinking_holds_lock_during_reload_and_displays_reply(self):
        chat1 = self.page.evaluate("activeChatId")
        gate1 = self.hold_chat(chat1)

        self.page.locator("#chat-input").fill("Сочи на майские")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        # Leave Chat 1 and come back while it is still thinking.
        self.page.locator("#new-chat").click()
        self.page.wait_for_function("id => activeChatId !== id && !chatBusy", arg=chat1)
        self.page.locator("#chat-list .trip", has_text="Сочи на майские").click()
        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Агент ещё отвечает…')"
        )
        self.assertTrue(self.page.locator("#chat-input").is_disabled())
        self.assertTrue(self.page.locator("#chat-stop").is_visible())

        # Hold the automatic reloadChat GET /api/chats/<chat1> to verify there is no unlock gap.
        held_reloads = []
        self.page.route(f"**/api/chats/{chat1}", lambda route: held_reloads.append(route))

        gate1.set()
        deadline_ms = 5000
        self.page.wait_for_function("streams.size === 0", timeout=deadline_ms)
        for _ in range(100):
            if held_reloads:
                break
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held_reloads), 1)

        # Stream has ended (#chat-stop hidden), yet input/send MUST stay disabled until reload finishes.
        self.assertTrue(self.page.locator("#chat-stop").is_hidden())
        self.assertTrue(self.page.locator("#chat-input").is_disabled())
        self.assertTrue(self.page.locator("#chat-send").is_disabled())
        self.assertTrue(self.page.evaluate("chatLocked()"))

        self.page.unroute(f"**/api/chats/{chat1}")
        held_reloads[0].continue_()

        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Ответ для: Сочи на майские') && !chatLocked()"
        )
        self.assertNotIn("Агент ещё отвечает…", self.page.locator("#chat-messages").inner_text())
        self.assertTrue(self.page.locator("#chat-input").is_enabled())
        self.assertEqual(self.page.evaluate("document.activeElement?.id"), "chat-input")

    def test_stop_after_returning_to_thinking_chat_shows_error_and_partial_reply(self):
        chat1 = self.page.evaluate("activeChatId")
        self.hold_chat(chat1)

        self.page.locator("#chat-input").fill("Долгий запрос")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        self.page.locator("#new-chat").click()
        self.page.wait_for_function("id => activeChatId !== id && !chatBusy", arg=chat1)
        self.page.locator("#chat-list .trip", has_text="Долгий запрос").click()
        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Агент ещё отвечает…')"
        )

        self.page.locator("#chat-stop").click()
        self.page.wait_for_function("!chatLocked()")
        self.assertNotIn("Агент ещё отвечает…", self.page.locator("#chat-messages").inner_text())
        self.assertIn("Ожидание остановлено", self.page.locator("#chat-error").inner_text())
        self.assertTrue(self.page.locator("#chat-reload").is_visible())

    def test_background_cart_updates_badge_and_unlocks_open_basket(self):
        chat1 = self.page.evaluate("activeChatId")
        gate1 = self.hold_chat(chat1)
        hotel = hotels.search(date(2027, 6, 10), date(2027, 6, 15))[0]

        def add_cart_in_background(session_id: str, _user_id: str):
            service._agent().update_state(service._config(session_id), {"trip": [
                {"op": "requirements", "changes": {
                    "check_in": "2027-06-10", "check_out": "2027-06-15", "adults": 2}},
                {"op": "cart_add", "item": hotel_item(hotel).model_dump(mode="json")},
            ]}, as_node="tools")

        self.stream_actions[chat1] = add_cart_in_background
        self.page.locator("#chat-input").fill("Подбери отель в корзину")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        # Switch to a new chat while Chat 1 is still running.
        self.page.locator("#new-chat").click()
        self.page.wait_for_function("id => activeChatId !== id && !chatBusy", arg=chat1)

        # Open basket while Chat 1 is still thinking: pay & remove must be disabled for Chat 1's cart.
        self.click_and_wait_refresh("#basket-toggle")
        self.assertEqual(self.page.locator(".purchase-card").count(), 1)
        self.assertTrue(self.page.locator(".purchase-card .pay").is_disabled())
        self.assertTrue(self.page.locator(".purchase-card .danger").is_disabled())
        self.assertTrue(self.page.locator(".purchase-card .text-link").is_enabled())

        # Close basket, reset local badge state to 0, then let Chat 1 finish in background while on Chat 2:
        # refreshPurchases() in streamAgent.finally must still update #basket-count to 1!
        self.page.locator("#basket-close").click()
        self.page.evaluate("purchases = { carts: [], orders: [] }; paintPurchases();")
        self.assertTrue(self.page.locator("#basket-count").is_hidden())

        gate1.set()
        self.page.wait_for_function(
            "streams.size === 0 && !document.querySelector('#basket-count').hidden && document.querySelector('#basket-count').textContent === '1'"
        )
        self.click_and_wait_refresh("#basket-toggle")
        self.assertTrue(self.page.locator(".purchase-card .pay").is_enabled())
        self.assertTrue(self.page.locator(".purchase-card .danger").is_enabled())

    def test_delete_thinking_chat_aborts_cleanly_without_error(self):
        chat1 = self.page.evaluate("activeChatId")
        self.hold_chat(chat1)

        self.page.locator("#chat-input").fill("Удаляемый чат")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        self.page.locator("#chat-list .delete-chat").first.click()
        self.page.locator("#confirm-accept").click()
        self.page.wait_for_function("id => activeChatId && activeChatId !== id && !chatBusy && !chatLocked()", arg=chat1)
        self.assertEqual(self.page.evaluate("streams.size"), 0)
        self.assertTrue(self.page.locator("#chat-error").is_hidden())


    def test_reload_error_clears_spinner_and_manual_reload_recovers(self):
        chat1 = self.page.evaluate("activeChatId")
        gate1 = self.hold_chat(chat1)

        self.page.locator("#chat-input").fill("Проверка сбоя подгрузки")
        self.page.locator("#chat-send").click()
        self.assertTrue(self.started[chat1].wait(timeout=5))

        self.page.locator("#new-chat").click()
        self.page.wait_for_function("id => activeChatId !== id && !chatBusy", arg=chat1)
        self.page.locator("#chat-list .trip", has_text="Проверка сбоя подгрузки").click()
        self.page.wait_for_function(
            "document.querySelector('#chat-messages').textContent.includes('Агент ещё отвечает…')"
        )

        self.page.route(
            f"**/api/chats/{chat1}",
            lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"Сбой подгрузки"}'),
        )
        gate1.set()
        self.page.wait_for_function("!chatLocked() && !document.querySelector('#chat-reload').hidden")
        self.assertNotIn("Агент ещё отвечает…", self.page.locator("#chat-messages").inner_text())
        self.assertIn("Сбой подгрузки", self.page.locator("#chat-error").inner_text())

        self.page.unroute(f"**/api/chats/{chat1}")
        self.click_and_wait_refresh("#chat-reload")
        self.assertIn("Ответ для: Проверка сбоя подгрузки", self.page.locator("#chat-messages").inner_text())
        self.assertTrue(self.page.locator("#chat-error").is_hidden())


if __name__ == "__main__":
    names = [name for name in ChatSwitchingWhileThinkingTests.__dict__ if name.startswith("test_")]
    suite = unittest.TestSuite([ChatSwitchingWhileThinkingTests(name) for name in sorted(names)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
