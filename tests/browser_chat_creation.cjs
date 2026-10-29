// Uses the real frontend and mocked API; no model or backend required.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let chats = [];
      let creates = 0;
      let messages = [];
      let createGate = null;
      let failCreate = false;
      const sent = [];
      await page.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== 'http://pulse.test') return route.abort();
        const fixtures = {
          '/api/auth/me': { user_id: 'test-user' },
          '/api/location': { city: 'Сочи', examples: [{ label: 'К морю на выходные', prompt: 'Hidden details must not be sent' }] },
          '/api/memory': { text: '', max_chars: 512 },
          '/api/purchases': { carts: [], orders: [] },
        };
        if (fixtures[url.pathname]) return route.fulfill({ json: fixtures[url.pathname] });
        if (url.pathname === '/api/chats') {
          if (request.method() === 'GET') return route.fulfill({ json: { chats } });
          creates++;
          if (createGate) await createGate;
          if (failCreate) return route.fulfill({ status: 503, json: { detail: 'Creation failed' } });
          const chat = { id: `chat-${creates}`, title: 'Новый чат', updated_at: '2026-10-29T10:00:00Z' };
          chats.unshift(chat);
          messages = [];
          return route.fulfill({ json: { chat, messages } });
        }
        if (url.pathname.startsWith('/api/chats/')) {
          if (request.method() === 'DELETE') {
            chats = chats.filter(chat => chat.id !== url.pathname.split('/').pop());
            return route.fulfill({ json: { ok: true } });
          }
          return route.fulfill({ json: { chat: chats[0], messages, trip: null } });
        }
        if (url.pathname === '/api/chat') {
          const payload = request.postDataJSON();
          sent.push(payload);
          const chat = chats.find(chat => chat.id === payload.session_id);
          assert(chat, 'A chat must exist before sending the message');
          chat.title = payload.message;
          messages = [{ role: 'user', content: payload.message }, { role: 'assistant', content: 'Reply' }];
          const events = [{ type: 'chat', chat }, { type: 'token', text: 'Reply' }, { type: 'done' }];
          return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') });
        }
        const files = { '/': 'index.html', '/static/style.css': 'style.css', '/static/app.js': 'app.js' };
        if (!files[url.pathname]) return route.fulfill({ status: 404 });
        return route.fulfill({
          body: await fs.readFile(path.join(__dirname, '../frontend', files[url.pathname])),
          contentType: url.pathname.endsWith('.css') ? 'text/css' : url.pathname.endsWith('.js') ? 'application/javascript' : 'text/html',
        });
      });
      const ready = () => page.waitForFunction(() => activeUserId && !chatLocked());
      await page.goto('http://pulse.test');
      await ready();
      assert.equal(creates, 0);
      assert.equal(await page.locator('#chat-list .trip').count(), 0);
      assert(await page.locator('#chat-input').isEnabled());
      await page.locator('#chat-input').fill('Draft only');
      assert.equal(creates, 0, 'Typing must not persist a chat');
      await page.reload();
      await ready();
      assert.equal(creates, 0, 'Loading an empty account must not create chats');

      // Hold creation to check double submits and conflicting actions.
      let release;
      createGate = new Promise(resolve => { release = resolve; });
      await page.locator('#chat-input').fill('First request');
      await page.locator('#chat-send').click();
      await page.waitForFunction(() => chatBusy);
      await page.evaluate(() => { void sendMessage('Duplicate'); newChatButton.click(); });
      assert(await page.locator('#chat-input').isDisabled());
      release();
      await ready();
      createGate = null;
      assert.equal(creates, 1);
      assert.deepEqual(sent, [{ session_id: 'chat-1', message: 'First request' }]);
      assert.equal(await page.locator('#chat-list .chat-name').innerText(), 'First request');
      await page.reload();
      await ready();
      assert.equal(creates, 1, 'Reload should restore the existing chat');
      assert.equal(await page.locator('#chat-input').inputValue(), '');
      assert.equal(await page.locator('#chat-messages .bubble.user').innerText(), 'First request');

      // Removing the last chat should return to the unsaved composer.
      await page.locator('#chats-toggle').click();
      await page.locator('.chat-options').click();
      await page.locator('.delete-chat').click();
      await page.locator('#confirm-accept').click();
      await page.waitForFunction(() => activeChatId === null && !chatBusy);
      await ready();
      assert.equal(creates, 1);
      assert.equal(await page.locator('#chat-list .trip').count(), 0);
      if (width <= 650) await page.evaluate(() => setChatsOpen(false));
      await page.waitForFunction(() => !confirmDialog.open && getComputedStyle(confirmDialog).display === 'none');

      // Preserve the request if creating its chat fails, then retry successfully.
      failCreate = true;
      await page.locator('#chat-input').fill('Retry this request');
      await page.locator('#chat-send').click();
      await page.waitForFunction(() => !chatBusy && !chatError.hidden);
      assert.equal(await page.locator('#chat-input').inputValue(), 'Retry this request');
      assert.equal(sent.length, 1);
      assert.equal(await page.locator('#chat-list .trip').count(), 0);
      failCreate = false;
      await page.locator('#chat-send').click();
      await ready();
      assert.equal(sent.at(-1).message, 'Retry this request');

      // Explicit creation still creates a visible empty chat.
      await page.locator('#new-chat').click();
      await ready();
      assert.equal(await page.locator('#chat-title').innerText(), 'Новый чат');
      const beforeSuggestion = sent.length;
      await page.locator('.suggestions .chip').first().click();
      await ready();
      assert.equal(sent.length, beforeSuggestion + 1);
      assert.equal(sent.at(-1).message, 'К морю на выходные');
      chats = [];
      await page.reload();
      await ready();
      const beforeDraftSuggestion = creates;
      await page.locator('.suggestions .chip').first().click();
      await ready();
      assert.equal(creates, beforeDraftSuggestion + 1);
      assert.equal(sent.at(-1).message, 'К морю на выходные');
      assert.deepEqual(errors, []);
      console.log(`${width}px: no automatic chats; first send, duplicates, reload, deletion, retry, explicit creation and suggestions OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
