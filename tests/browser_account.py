"""Focused account panel checks with the shared isolated browser harness."""
import unittest
from pathlib import Path
from browser_purchases import BasketStatusTests


class AccountPanelTests(BasketStatusTests):
    def test_order_timeline(self):
        self.page.locator('#profile-toggle').click()
        self.page.locator('#tab-orders').click()
        self.page.wait_for_function('window.qaPendingRefreshes === 0')
        self.page.evaluate("""() => {
          purchases.orders = [
            {id: 'past', status: 'confirmed', total_rub: 12000, items: [{kind: 'hotel', title: 'Тестовый отель', price_rub: 12000, snapshot: {name: 'Тестовый отель', check_in: '2025-09-10', check_out: '2025-09-17'}}]},
            {id: 'future', status: 'confirmed', total_rub: 18000, items: [{kind: 'hotel', title: 'Будущая поездка', price_rub: 18000, snapshot: {check_in: '2028-06-10', check_out: '2028-06-17'}}]}
          ];
          paintOrderHistory();
        }""")
        self.assertEqual(self.page.locator('.history-trip').count(), 1)
        self.assertIn('Будущая поездка', self.page.locator('#profile-orders').inner_text())
        self.page.locator('[data-mode="past"]').click()
        self.assertIn('сентябрь 2025', self.page.locator('.history-month').inner_text().lower())
        self.assertEqual(self.page.locator('.history-trip.is-past').count(), 1)
        self.page.locator('.history-summary').click()
        self.assertTrue(self.page.locator('.history-remove').is_visible())
        self.page.set_viewport_size({'width': 320, 'height': 568})
        self.assertTrue(self.page.locator('#account-panel').evaluate('(e) => e.scrollWidth <= e.clientWidth'))
        self.page.evaluate("""() => {
            const sample = purchases.orders[0];
            purchases.orders = Array.from({length: 40}, (_, i) => ({...sample, id: 'overflow-' + i}));
            paintOrderHistory();
        }""")
        for width, height in [(320, 568), (844, 390), (1280, 800)]:
            self.page.set_viewport_size({'width': width, 'height': height})
            self.assertTrue(self.page.locator('.history-scroll').evaluate('(e) => e.scrollHeight > e.clientHeight'))
            self.assertTrue(self.page.locator('#account-panel').evaluate('(e) => e.scrollHeight <= e.clientHeight'))
            before = self.page.locator('.history-filters').bounding_box()['y']
            self.page.locator('.history-scroll').evaluate('(e) => e.scrollTop = 300')
            self.assertEqual(self.page.locator('.history-filters').bounding_box()['y'], before)
        self.page.screenshot(path='artifacts/account-panel/orders-timeline.png')

    def test_account_tabs_and_memory(self):
        self.page.locator('#profile-toggle').click()
        self.page.locator('#tab-memory').click()
        self.page.wait_for_function("!document.querySelector('#memory-text').disabled")
        self.page.locator('#memory-text').fill('<b>Тихий отель</b>')
        self.page.locator('#save-memory').click()
        self.page.wait_for_function("document.querySelector('#memory-status').textContent === 'Сохранено'")
        self.page.locator('#tab-orders').click()
        self.assertTrue(self.page.locator('#pane-code').is_hidden())
        self.page.locator('#tab-orders').press('ArrowRight')
        self.assertEqual(self.page.locator('#memory-text').input_value(), '<b>Тихий отель</b>')
        self.page.locator('#memory-text').fill('Черновик')
        self.page.locator('#reset-memory').click()
        self.assertEqual(self.page.locator('#memory-text').input_value(), '<b>Тихий отель</b>')
        Path('artifacts/account-panel').mkdir(parents=True, exist_ok=True)
        for width, height in [(1280, 800), (390, 844), (320, 568), (844, 390)]:
            self.page.set_viewport_size({'width': width, 'height': height})
            for tab in ['code', 'orders', 'memory']:
                self.page.locator('#tab-' + tab).click()
                box = self.page.locator('#account-panel').bounding_box()
                self.assertGreaterEqual(box['x'], 0)
                self.assertLessEqual(box['x'] + box['width'], width)
                self.assertLessEqual(box['y'] + box['height'], height)
                self.assertTrue(self.page.locator('#account-panel').evaluate('(e) => e.scrollWidth <= e.clientWidth'))
                self.page.screenshot(path=f'artifacts/account-panel/{width}-{tab}.png')
        self.page.locator('#forget').click()
        self.page.locator('#confirm-accept').click()
        self.page.wait_for_function("document.querySelector('#memory-status').textContent === 'Память очищена'")
        self.assertEqual(self.page.locator('#memory-text').input_value(), '')


if __name__ == '__main__':
    suite = unittest.TestSuite([AccountPanelTests('test_account_tabs_and_memory'), AccountPanelTests('test_order_timeline')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
