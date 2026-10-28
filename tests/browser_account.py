"""Focused account panel checks with the shared isolated browser harness."""
import unittest
from pathlib import Path
from browser_purchases import BasketStatusTests


class AccountPanelTests(BasketStatusTests):
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
    suite = unittest.TestSuite([AccountPanelTests('test_account_tabs_and_memory')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
