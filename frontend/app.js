const idEl = document.querySelector('#user-id');
const codeEl = document.querySelector('#code-box');
const messageEl = document.querySelector('#message');
const showButton = document.querySelector('#show-code');
const form = document.querySelector('#login-form');
const accountView = document.querySelector('#account-view');
const loginView = document.querySelector('#login-view');
const accountTitle = document.querySelector('#account-title');
const loginCode = document.querySelector('#login-code');
const loginError = document.querySelector('#login-error');
const loginSubmit = document.querySelector('#login-submit');
const toggleLoginCode = document.querySelector('#toggle-login-code');
const accountPanel = document.querySelector('#account-panel');
const profileToggle = document.querySelector('#profile-toggle');
const closePanelButton = document.querySelector('#close-panel');
const panelDrag = document.querySelector('#panel-drag');
let currentCode = null;
let visible = false;
let dragOffset = null;

function keepPanelInViewport() {
  if (accountPanel.hidden) return;
  const rect = accountPanel.getBoundingClientRect();
  const minLeft = window.innerWidth <= 650
    ? 62
    : window.innerWidth <= 1050
      ? 172
      : Math.max(202, (window.innerWidth - 1600) / 2 + 202);
  const left = Math.min(Math.max(rect.left, minLeft), Math.max(minLeft, window.innerWidth - rect.width - 8));
  const top = Math.min(Math.max(rect.top, 8), Math.max(8, window.innerHeight - rect.height - 8));
  accountPanel.style.left = `${left}px`;
  accountPanel.style.top = `${top}px`;
}

panelDrag.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || event.target.closest('button')) return;
  const rect = accountPanel.getBoundingClientRect();
  dragOffset = { id: event.pointerId, x: event.clientX - rect.left, y: event.clientY - rect.top };
  panelDrag.setPointerCapture(event.pointerId);
  accountPanel.classList.add('dragging');
  event.preventDefault();
});
panelDrag.addEventListener('pointermove', (event) => {
  if (!dragOffset || event.pointerId !== dragOffset.id) return;
  accountPanel.style.left = `${event.clientX - dragOffset.x}px`;
  accountPanel.style.top = `${event.clientY - dragOffset.y}px`;
  keepPanelInViewport();
});
function endDrag(event) {
  if (!dragOffset || event.pointerId !== dragOffset.id) return;
  dragOffset = null;
  accountPanel.classList.remove('dragging');
}
panelDrag.addEventListener('pointerup', endDrag);
panelDrag.addEventListener('pointercancel', endDrag);
window.addEventListener('resize', keepPanelInViewport);

function setPanelOpen(open) {
  accountPanel.hidden = !open;
  if (open) keepPanelInViewport();
  document.body.classList.toggle('panel-open', open);
  profileToggle.setAttribute('aria-expanded', String(open));
  profileToggle.setAttribute('aria-label', open ? 'Свернуть профиль' : 'Открыть профиль');
  if (open) closePanelButton.focus();
  else {
    setLoginMode(false);
    profileToggle.focus();
  }
}

function setLoginMode(active) {
  accountView.hidden = active;
  loginView.hidden = !active;
  accountTitle.textContent = active ? 'Войти в аккаунт' : 'Мой аккаунт';
  loginError.hidden = true;
  loginError.textContent = '';
  if (active) {
    loginCode.focus();
  } else {
    form.reset();
    loginCode.type = 'password';
    toggleLoginCode.textContent = 'Показать код';
    toggleLoginCode.setAttribute('aria-pressed', 'false');
  }
  keepPanelInViewport();
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
  setLoginMode(false);
  idEl.textContent = data.user_id;
  currentCode = data.code || null;
  visible = false;
  codeEl.textContent = currentCode ? '•'.repeat(32) : 'Код скрыт. Если вы его не сохранили, перевыпустите код';
  codeEl.disabled = !currentCode;
  codeEl.setAttribute('aria-label', currentCode ? 'Код доступа. Нажмите, чтобы скопировать' : 'Код доступа недоступен');
  showButton.hidden = !currentCode;
  showButton.textContent = 'Показать код';
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
codeEl.addEventListener('click', async () => {
  if (!currentCode) return;
  try {
    await navigator.clipboard.writeText(currentCode);
    messageEl.textContent = 'Код скопирован';
  } catch {
    visible = true;
    codeEl.textContent = currentCode;
    showButton.textContent = 'Скрыть код';
    messageEl.textContent = 'Не удалось скопировать. Скопируйте показанный код вручную.';
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
  setLoginMode(true);
});
document.querySelector('#cancel-login').addEventListener('click', () => {
  setLoginMode(false);
  document.querySelector('#open-login').focus();
});
toggleLoginCode.addEventListener('click', () => {
  const showing = loginCode.type === 'password';
  loginCode.type = showing ? 'text' : 'password';
  toggleLoginCode.textContent = showing ? 'Скрыть код' : 'Показать код';
  toggleLoginCode.setAttribute('aria-pressed', String(showing));
  loginCode.focus();
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const code = loginCode.value.trim();
  loginError.hidden = true;
  loginSubmit.disabled = true;
  loginSubmit.textContent = 'Входим…';
  try {
    render(await api('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
    }));
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
    loginCode.focus();
  } finally {
    loginSubmit.disabled = false;
    loginSubmit.textContent = 'Войти';
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || accountPanel.hidden) return;
  if (!loginView.hidden) setLoginMode(false);
  else setPanelOpen(false);
});
initialize();
