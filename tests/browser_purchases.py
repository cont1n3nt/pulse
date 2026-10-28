"""Browser regression tests: .venv/bin/python tests/browser_purchases.py -v.

Requires Playwright and its Chromium browser. Uses a temporary database and no LLM.
"""
import json
import os
import re
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TEMP = tempfile.TemporaryDirectory(prefix="pulse-browser-tests-", ignore_cleanup_errors=True)
os.environ["DATA_DIR"] = _TEMP.name + "/agent"
os.environ["PULSE_DB_PATH"] = _TEMP.name + "/app.sqlite3"
os.environ["PULSE_AUTH_KEY"] = "browser-test-secret-" * 4
os.environ["LLM_API_KEY"] = "test-key-no-network"
os.environ["LANGSMITH_TRACING"] = "false"

import uvicorn
from playwright.sync_api import sync_playwright
from backend.main import create_app
from backend.agent import service
from backend.agent.adapters import hotels
from backend.agent.trip import hotel_item


class BasketStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket = socket.socket()
        cls.socket.bind(("127.0.0.1", 0))
        cls.url = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
        cls.app = create_app()
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, log_level="error"))
        cls.thread = threading.Thread(
            target=cls.server.run, kwargs={"sockets": [cls.socket]}, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(.05)
        if not cls.server.started:
            raise RuntimeError("Test server did not start")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.socket.close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.context.route(re.compile(r'https?://(?!127\.0\.0\.1(?=[:/]))'),
                           lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.errors = []
        self.refresh_failures = []
        self.held_refreshes = None
        self.page.route("**/api/purchases", self.route_refresh)
        self.page.route("**/api/hotels/*/photo", lambda route: route.fulfill(status=404))
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("dialog", lambda dialog: (self.errors.append("Unexpected browser dialog"), dialog.dismiss()))
        # Track completion of the real refresh function, including JSON parsing/rendering.
        # networkidle may still refer to the previous page state immediately after a click.
        self.page.add_init_script("""
            window.qaPendingRefreshes = 0;
            window.qaCompletedRefreshes = 0;
            window.addEventListener('DOMContentLoaded', () => {
                const originalRefresh = refreshPurchases;
                refreshPurchases = async (...args) => {
                    window.qaPendingRefreshes++;
                    try { return await originalRefresh(...args); }
                    finally {
                        window.qaPendingRefreshes--;
                        window.qaCompletedRefreshes++;
                    }
                };
            });
        """)
        # Optional baseline allows verifying the regression against an older app.js.
        if source := os.getenv("PULSE_QA_APP_JS"):
            self.page.route("**/static/app.js?*", lambda route: route.fulfill(
                path=source, content_type="application/javascript"))
        self.page.goto(self.url, wait_until="networkidle")
        self.page.wait_for_function("activeChatId && !chatBusy")
        self.page.locator("#close-panel").click()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def status(self):
        return self.page.locator("#basket-status").inner_text()

    def refresh(self):
        self.page.evaluate("refreshPurchases()")

    def click_and_wait_refresh(self, selector):
        completed = self.page.evaluate("window.qaCompletedRefreshes")
        self.page.locator(selector).click()
        if self.page.locator('#confirm-dialog').is_visible():
            self.page.locator('#confirm-accept').click()
        self.page.wait_for_function(
            "count => !chatBusy && !purchaseBusy && window.qaCompletedRefreshes > count && window.qaPendingRefreshes === 0",
            arg=completed)

    def fail_refresh(self, message="Temporary load failure"):
        self.refresh_failures.append(message)

    def route_refresh(self, route):
        if self.held_refreshes is not None:
            self.held_refreshes.append(route)
        elif self.refresh_failures:
            route.fulfill(status=503, content_type="application/json",
                          body=json.dumps({"detail": self.refresh_failures.pop(0)}))
        else:
            route.continue_()

    def seed_cart(self):
        chat = self.page.evaluate("activeChatId")
        hotel = hotels.search(date(2027, 6, 10), date(2027, 6, 15))[0]
        service._agent().update_state(service._config(chat), {"trip": [
            {"op": "requirements", "changes": {
                "check_in": "2027-06-10", "check_out": "2027-06-15", "adults": 2}},
            {"op": "cart_add", "item": hotel_item(hotel).model_dump(mode="json")},
        ]}, as_node="tools")
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_function("!chatBusy && purchases.carts.length === 1")
        self.click_and_wait_refresh("#basket-toggle")
        return chat

    def test_train_timeline_and_dynamic_location_survive_new_layout(self):
        from backend.agent import packages
        from backend.agent.contracts import Requirements
        start, end = date(2027, 6, 10), date(2027, 6, 15)
        req = Requirements(check_in=start, check_out=end, adults=2, transport='train')
        built = packages.build(req, hotels.search(start, end, limit=15), None, None, rail_from='MOW')
        chat = self.page.evaluate('activeChatId')
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'requirements', 'changes': req.model_dump(mode='json')},
            {'op': 'packages', 'items': [p.model_dump(mode='json') for p in built]},
            {'op': 'cart_set', 'items': [i.model_dump(mode='json') for i in packages.items(built[0])]},
        ]}, as_node='tools')
        self.page.reload(wait_until='networkidle')
        self.page.wait_for_function('!chatBusy && purchases.carts.length === 1')
        self.assertIn('поезд + трансфер + отель', self.page.locator('#package-note').inner_text())
        self.assertIn('Поезд', self.page.locator('#trip-cart .timeline').inner_text())
        self.assertEqual(self.page.evaluate('locationCity'), 'Сочи')
        self.click_and_wait_refresh('#basket-toggle')
        self.assertEqual(self.page.locator('.purchase-card').count(), 1)

    def test_confirmation_cancel_keeps_cart_and_basket_open(self):
        self.seed_cart()
        remove = self.page.locator('.purchase-card .danger')
        remove.click()
        self.assertTrue(self.page.locator('#confirm-dialog').is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'confirm-cancel')
        # Tab stays within the top dialog, including when the basket is open below it.
        for _ in range(5):
            self.page.keyboard.press('Tab')
            self.assertTrue(self.page.evaluate('document.activeElement.closest("#confirm-dialog") !== null'))
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#confirm-dialog').is_hidden())
        self.assertTrue(self.page.locator('#basket-dialog').is_visible())
        self.assertTrue(remove.evaluate('(button) => button === document.activeElement'))
        self.assertEqual(self.page.evaluate('purchases.carts.length'), 1)
        remove.click()
        self.page.locator('#confirm-cancel').click()
        self.assertEqual(self.page.evaluate('purchases.carts.length'), 1)

    def test_mobile_confirmation_fits_and_closes_on_backdrop(self):
        self.page.set_viewport_size({'width': 320, 'height': 640})
        self.click_and_wait_refresh('#profile-toggle')
        self.page.locator('#rotate').click()
        dialog = self.page.locator('#confirm-dialog')
        self.assertTrue(dialog.is_visible())
        self.assertTrue(dialog.evaluate('(node) => node.scrollWidth === node.clientWidth && node.getBoundingClientRect().right <= innerWidth'))
        self.page.mouse.click(2, 2)
        self.assertTrue(dialog.is_hidden())
        self.assertTrue(self.page.locator('#account-panel').is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'rotate')

    def test_replacement_cart_confirmation_and_cancelled_history(self):
        chat = self.seed_cart()
        self.click_and_wait_refresh('.purchase-card .pay')
        original = self.page.evaluate('purchases.orders[0].id')
        hotel = hotels.search(date(2027, 6, 10), date(2027, 6, 15))[0]
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'cart_set', 'items': [hotel_item(hotel).model_dump(mode='json')],
             'order_change': {'order_id': original, 'mode': 'replace'}},
        ]}, as_node='tools')
        self.refresh()
        self.assertIn('Перенос заказа ' + original, self.page.locator('.purchase-card').inner_text())
        self.assertIn('Прежний заказ отменится', self.page.locator('.purchase-card').inner_text())
        self.click_and_wait_refresh('.purchase-card .pay')
        self.page.locator('#basket-close').click()
        self.click_and_wait_refresh('#profile-toggle')
        self.assertEqual(self.page.locator('#profile-orders .order-box.cancelled').count(), 1)
        self.assertIn('отменён (перенесён)', self.page.locator('#profile-orders .order-box.cancelled').inner_text())

    def test_recovery_clears_only_load_error_and_restores_profile(self):
        self.click_and_wait_refresh("#basket-toggle")
        for message in ("First failure", "Second failure"):
            self.fail_refresh(message)
            self.refresh()
            self.assertEqual(self.status(), message)
            self.assertEqual(self.page.locator("#profile-orders .form-error").count(), 1)
        self.refresh()
        self.assertEqual(self.status(), "")
        self.assertEqual(self.page.locator("#profile-orders .form-error").count(), 0)
        self.assertIn("Пока нет оформленных заказов", self.page.locator("#profile-orders").inner_text())

    def test_profile_retry_clears_basket_error(self):
        self.click_and_wait_refresh("#profile-toggle")
        self.fail_refresh()
        self.refresh()
        self.page.locator("#tab-orders").click()
        self.page.locator("#profile-orders").get_by_role("button", name="Повторить").click()
        self.page.wait_for_function('!document.querySelector("#profile-orders .form-error")')
        self.assertEqual(self.status(), "")

    def test_payment_confirmation_survives_failed_refresh_and_recovery(self):
        self.seed_cart()
        self.fail_refresh()
        self.click_and_wait_refresh(".purchase-card .pay")
        self.page.wait_for_function('!purchaseBusy && document.querySelector("#profile-orders .form-error")')
        self.assertIn("оформлен и сохранён в профиле", self.status())
        self.assertIn("Temporary load failure", self.status())
        self.refresh()
        self.assertIn("оформлен и сохранён в профиле", self.status())
        self.assertNotIn("Temporary load failure", self.status())
        self.assertEqual(self.page.locator("#profile-orders .order-box").count(), 1)
        self.assertEqual(self.page.locator(".purchase-card").count(), 0)

    def test_remove_confirmation_survives_refresh(self):
        self.seed_cart()
        self.click_and_wait_refresh(".purchase-card .danger")
        self.page.wait_for_function("!purchaseBusy && purchases.carts.length === 0")
        self.refresh()
        self.assertEqual(self.status(), "Поездка удалена из корзины.")

    def test_refresh_during_payment_keeps_progress_and_then_result(self):
        self.seed_cart()
        payments = []
        self.page.route("**/api/purchases/*/pay", lambda route: payments.append(route))
        self.page.locator(".purchase-card .pay").click()
        self.page.locator('#confirm-accept').click()
        self.page.wait_for_function("purchaseBusy")
        self.fail_refresh()
        self.refresh()
        self.assertIn("Оформляем поездку…", self.status())
        self.assertIn("Temporary load failure", self.status())
        self.refresh()
        self.assertEqual(self.status(), "Оформляем поездку…")
        self.assertEqual(len(payments), 1)
        payments[0].continue_()
        self.page.wait_for_function("!purchaseBusy && purchases.orders.length === 1")
        self.assertIn("оформлен и сохранён в профиле", self.status())

    def test_mobile_reopening_recovers_without_stale_error(self):
        self.page.set_viewport_size({"width": 320, "height": 640})
        self.click_and_wait_refresh("#basket-toggle")
        self.fail_refresh()
        self.refresh()
        self.page.locator("#basket-close").click()
        self.click_and_wait_refresh("#basket-toggle")
        self.assertEqual(self.status(), "")
        self.assertTrue(self.page.locator("#basket-dialog").is_visible())
        self.assertFalse(self.page.locator("#basket-dialog").evaluate(
            "element => element.scrollWidth > element.clientWidth"))

    def test_rejected_payment_message_survives_refresh(self):
        chat = self.seed_cart()
        service._agent().update_state(service._config(chat), {"trip": [
            {"op": "requirements", "changes": {"adults": 3}}]}, as_node="tools")
        with self.page.expect_response("**/api/purchases/*/pay") as payment:
            self.click_and_wait_refresh(".purchase-card .pay")
        self.assertEqual(payment.value.status, 409, payment.value.text())
        self.assertIn("Поездка изменилась", self.status())
        self.fail_refresh()
        self.refresh()
        self.refresh()
        self.assertEqual(self.status(), "Поездка изменилась. Проверьте обновлённую корзину.")
        self.assertEqual(self.page.evaluate("purchases.orders.length"), 0)

    def hold_refreshes(self):
        self.held_refreshes = []
        return self.held_refreshes

    def start_refresh(self, routes, count):
        self.page.evaluate("void refreshPurchases()")
        deadline = time.monotonic() + 5
        while len(routes) < count and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(routes), count, self.page.evaluate("""({
            user: activeUserId, request: purchasesRequest, status: basketStatus.textContent,
            pending: window.qaPendingRefreshes, completed: window.qaCompletedRefreshes
        })"""))

    def resolve(self, route, error=None):
        with self.page.expect_request_finished():
            route.fulfill(status=503 if error else 200, content_type="application/json",
                          body=json.dumps({"detail": error} if error else {"carts": [], "orders": []}))
        self.page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")

    def test_old_failure_cannot_replace_new_success(self):
        routes = self.hold_refreshes()
        self.start_refresh(routes, 1)
        self.start_refresh(routes, 2)
        self.resolve(routes[1])
        self.resolve(routes[0], "Old failure")
        self.assertEqual(self.status(), "")

    def test_old_success_cannot_clear_new_failure(self):
        routes = self.hold_refreshes()
        self.start_refresh(routes, 1)
        self.start_refresh(routes, 2)
        self.resolve(routes[1], "Current failure")
        self.resolve(routes[0])
        self.assertEqual(self.status(), "Current failure")

    def test_account_switch_clears_messages_and_ignores_previous_response(self):
        self.seed_cart()
        self.click_and_wait_refresh(".purchase-card .danger")
        self.page.wait_for_function("!purchaseBusy && purchases.carts.length === 0")
        self.fail_refresh()
        self.refresh()
        routes = self.hold_refreshes()
        self.start_refresh(routes, 1)
        previous_user = self.page.evaluate("activeUserId")
        self.context.clear_cookies()
        self.page.evaluate("async () => render(await api('/api/auth/guest', {method: 'POST'}))")
        self.page.wait_for_function("id => activeUserId !== id && !chatBusy", arg=previous_user)
        self.assertEqual(self.status(), "")
        self.resolve(routes[0], "Previous account failure")
        self.assertEqual(self.status(), "")
        self.assertEqual(self.page.locator("#profile-orders .form-error").count(), 0)
        self.assertEqual(len(routes), 2)
        self.resolve(routes[1])


if __name__ == "__main__":
    unittest.main()
