const idEl = document.querySelector('#user-id');
const codeEl = document.querySelector('#code-box');
const messageEl = document.querySelector('#message');
const showButton = document.querySelector('#show-code');
const copyButton = document.querySelector('#copy-code');
const form = document.querySelector('#login-form');
const accountPanel = document.querySelector('#account-panel');
const profileToggle = document.querySelector('#profile-toggle');
const closePanelButton = document.querySelector('#close-panel');
let currentCode = null;
let visible = false;

function setPanelOpen(open) {
  accountPanel.hidden = !open;
  document.body.classList.toggle('panel-open', open);
  profileToggle.setAttribute('aria-expanded', String(open));
  profileToggle.setAttribute('aria-label', open ? 'Свернуть профиль' : 'Открыть профиль');
  if (open) closePanelButton.focus();
  else profileToggle.focus();
}

profileToggle.addEventListener('click', () => setPanelOpen(accountPanel.hidden));
closePanelButton.addEventListener('click', () => setPanelOpen(false));

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.detail || 'Не удалось выполнить запрос');
    error.status = response.status;
    throw error;
  }
  return data;
}

function render(data) {
  idEl.textContent = data.user_id;
  currentCode = data.code || null;
  visible = false;
  codeEl.textContent = currentCode ? '•'.repeat(32) : 'Код скрыт. Если вы его не сохранили, перевыпустите код';
  showButton.hidden = !currentCode;
  showButton.textContent = 'Показать код';
  copyButton.hidden = !currentCode;
  messageEl.textContent = '';
  if (currentCode) setPanelOpen(true);
}

async function initialize() {
  try {
    render(await api('/api/auth/me'));
  } catch (error) {
    if (error.status !== 401) {
      setPanelOpen(true);
      messageEl.textContent = error.message;
      return;
    }
    try {
      render(await api('/api/auth/guest', { method: 'POST' }));
    } catch (guestError) {
      setPanelOpen(true);
      messageEl.textContent = guestError.message;
    }
  }
}

showButton.addEventListener('click', () => {
  visible = !visible;
  codeEl.textContent = visible ? currentCode : '•'.repeat(32);
  showButton.textContent = visible ? 'Скрыть код' : 'Показать код';
});
copyButton.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(currentCode);
    messageEl.textContent = 'Код скопирован';
  } catch {
    messageEl.textContent = 'Не удалось скопировать. Покажите код и скопируйте вручную.';
  }
});
document.querySelector('#rotate').addEventListener('click', async () => {
  if (!confirm('Старый код перестанет работать. Перевыпустить код?')) return;
  try {
    render(await api('/api/auth/rotate', { method: 'POST' }));
    messageEl.textContent = 'Сохраните новый код доступа';
  } catch (error) {
    messageEl.textContent = error.message;
  }
});
document.querySelector('#open-login').addEventListener('click', () => {
  form.hidden = false;
  document.querySelector('#login-code').focus();
});
document.querySelector('#cancel-login').addEventListener('click', () => {
  form.reset();
  form.hidden = true;
  messageEl.textContent = '';
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const code = document.querySelector('#login-code').value.trim();
  try {
    render(await api('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
    }));
    form.reset();
    form.hidden = true;
  } catch (error) {
    messageEl.textContent = error.message;
  }
});
initialize();
