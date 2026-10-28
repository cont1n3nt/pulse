// Run with Playwright installed: node tests/browser_panels.cjs
// Uses the real frontend and mocked API responses; no backend or API key needed.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const chat = { id: 'test-chat', title: 'Тестовая поездка', updated_at: '2026-10-28T10:00:00Z' };
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://pulse.test') return route.abort();
      const fixtures = {
        '/api/auth/me': { user_id: 'test-user' },
        '/api/location': { city: 'Сочи', examples: [] },
        '/api/chats': { chats: [chat] },
        '/api/chats/test-chat': { chat, messages: [], trip: null },
        '/api/purchases': { carts: [], orders: [] },
      };
      if (fixtures[url.pathname]) return route.fulfill({ json: fixtures[url.pathname] });
      const files = { '/': 'index.html', '/static/style.css': 'style.css', '/static/app.js': 'app.js' };
      if (!files[url.pathname]) return route.fulfill({ status: 404 });
      return route.fulfill({
        body: await fs.readFile(path.join(__dirname, '../frontend', files[url.pathname])),
        contentType: url.pathname.endsWith('.css') ? 'text/css' : url.pathname.endsWith('.js') ? 'application/javascript' : 'text/html',
      });
    });
    const expanded = async (id, value) => {
      await page.waitForFunction(({ id, value }) => document.getElementById(id).getAttribute('aria-expanded') === String(value), { id, value });
    };
    const width = selector => page.locator(selector).evaluate(el => el.getBoundingClientRect().width);
    for (const viewport of [1440, 1920, 2560, 3840]) {
      await page.setViewportSize({ width: viewport, height: 1080 });
      await page.goto('http://pulse.test');
      await page.waitForFunction(() => document.querySelector('#chat-title').textContent === 'Тестовая поездка');
      await expanded('chats-toggle', true);
      await expanded('trip-toggle', true);
      assert(await page.locator('#chats-toggle').isVisible());
      assert.equal(await page.locator('#trip-toggle').isVisible(), false);
      const initial = await width('.assistant');
      await page.click('#chats-toggle');
      await expanded('chats-toggle', false);
      assert.equal(await page.locator('#chat-list-panel').isVisible(), false);
      assert(await width('.assistant') > initial);
      assert(await page.locator('#profile-toggle').isVisible());
      assert(await page.locator('#basket-toggle').isVisible());
      await page.click('#trip-close');
      await expanded('trip-toggle', false);
      assert(await page.locator('#trip-toggle').isVisible());
      assert.equal(await page.locator('#trip-panel').isVisible(), false);
      assert(await width('.assistant') > initial);
      await page.click('#trip-toggle');
      await page.click('#chats-toggle');
      await expanded('chats-toggle', true);
      await expanded('trip-toggle', true);
      assert.equal(await page.locator('#trip-toggle').isVisible(), false);
      assert(Math.abs(await width('.assistant') - initial) < 1);
      // Selecting a chat must leave the desktop sidebar expanded.
      await page.locator('#chat-list .trip').click();
      await expanded('chats-toggle', true);
      assert.equal(await page.locator('#trip-backdrop').isVisible(), false);
      console.log(`Desktop ${viewport}px: collapse/expand both panels OK`);
    }
    for (const viewport of [1199, 900, 651, 650, 390]) {
      await page.setViewportSize({ width: viewport, height: 900 });
      await expanded('trip-toggle', false);
      await page.click('#trip-toggle');
      await expanded('trip-toggle', true);
      assert.equal(await page.locator('#trip-toggle').isVisible(), false);
      assert(await page.locator('#trip-backdrop').isVisible());
      assert(await page.locator('#trip-close').evaluate(el => el === document.activeElement));
      await page.click('#trip-close');
      await expanded('trip-toggle', false);
      assert(await page.locator('#trip-toggle').evaluate(el => el === document.activeElement));
      if (viewport <= 650) {
        await expanded('chats-toggle', false);
        await page.click('#chats-toggle');
        assert(await page.locator('#chat-list-panel').isVisible());
        await page.locator('#chat-list .trip').click();
        await expanded('chats-toggle', false);
        await page.click('#trip-toggle');
        await page.keyboard.press('Escape');
        await expanded('trip-toggle', false);
      }
      console.log(`Responsive ${viewport}px: drawers and controls OK`);
    }
    // Desktop preferences survive crossing both responsive breakpoints.
    await page.setViewportSize({ width: 1920, height: 1080 });
    await expanded('chats-toggle', true);
    await expanded('trip-toggle', true);
    await page.click('#chats-toggle');
    await page.click('#trip-close');
    await page.setViewportSize({ width: 390, height: 844 });
    await expanded('trip-toggle', false);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await expanded('chats-toggle', false);
    await expanded('trip-toggle', false);
    await page.click('#chats-toggle');
    await page.click('#trip-toggle');
    assert.equal(await page.locator('#trip-panel').evaluate(el => el.inert), false);
    assert.deepEqual(errors, []);
    console.log('Resize preferences and JavaScript errors: OK');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
