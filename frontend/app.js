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
const chatForm = document.querySelector('#chat-form');
const chatInput = document.querySelector('#chat-input');
const chatSend = document.querySelector('#chat-send');
const chatMessages = document.querySelector('#chat-messages');
const chatEmpty = document.querySelector('#chat-empty');
const chatError = document.querySelector('#chat-error');
const chatList = document.querySelector('#chat-list');
const chatTitle = document.querySelector('#chat-title');
const newChatButton = document.querySelector('#new-chat');
const chatsToggle = document.querySelector('#chats-toggle');
let activeUserId = null;
let activeChatId = null;
let chats = [];
let chatBusy = true;
let accountEpoch = 0;
let currentCode = null;
let visible = false;
let dragOffset = null;

function keepPanelInViewport() {
  if (accountPanel.hidden) return;
  const rect = accountPanel.getBoundingClientRect();
  const minLeft = 8;
  const left = Math.min(Math.max(rect.left, minLeft), Math.max(minLeft, window.innerWidth - rect.width - 8));
  const top = Math.min(Math.max(rect.top, 8), Math.max(8, window.innerHeight - rect.height - 8));
  accountPanel.style.left = `${left}px`;
  accountPanel.style.top = `${top}px`;
}

function centerPanel() {
  if (accountPanel.hidden) return;
  const rect = accountPanel.getBoundingClientRect();
  accountPanel.style.left = `${Math.max(8, (window.innerWidth - rect.width) / 2)}px`;
  accountPanel.style.top = `${Math.max(8, (window.innerHeight - rect.height) / 2)}px`;
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
  if (open) centerPanel();
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
  centerPanel();
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

function setChatBusy(busy) {
  chatBusy = busy;
  newChatButton.disabled = !activeUserId || busy;
  chatSend.disabled = !activeChatId || busy;
  chatInput.disabled = !activeChatId || busy;
  chatList.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
}

function setChatsOpen(open) {
  document.body.classList.toggle('chats-open', open);
  chatsToggle.setAttribute('aria-expanded', String(open));
  chatsToggle.setAttribute('aria-label', open ? 'Скрыть чаты' : 'Показать чаты');
}
chatsToggle.addEventListener('click', () => setChatsOpen(!document.body.classList.contains('chats-open')));

function showChatError(error) {
  chatError.textContent = error.message;
  chatError.hidden = false;
}

function renderChatList() {
  chatList.replaceChildren();
  for (const item of chats) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `trip${item.id === activeChatId ? ' active' : ''}`;
    button.setAttribute('aria-current', item.id === activeChatId ? 'true' : 'false');
    button.textContent = item.title;
    const date = document.createElement('small');
    date.textContent = new Date(item.updated_at).toLocaleDateString('ru-RU');
    button.append(date);
    button.disabled = chatBusy;
    button.addEventListener('click', () => selectChat(item.id));
    chatList.append(button);
  }
}

function clearChat() {
  activeChatId = null;
  chatMessages.replaceChildren(chatEmpty);
  chatEmpty.hidden = false;
  chatInput.value = '';
  resizeChatInput();
  chatError.hidden = true;
}

function displayChat(data) {
  clearChat();
  activeChatId = data.chat.id;
  chatTitle.textContent = data.chat.title;
  for (const message of data.messages) {
    addChatMessage(message.role === 'assistant' ? 'bot' : 'user', message.content);
  }
  renderChatList();
  setChatsOpen(false);
}

async function loadUserChats(userId) {
  activeUserId = userId;
  const epoch = ++accountEpoch;
  chats = [];
  clearChat();
  chatTitle.textContent = 'Загрузка чатов…';
  renderChatList();
  setChatBusy(true);
  try {
    const list = await api('/api/chats');
    if (epoch !== accountEpoch) return;
    chats = list.chats;
    const data = chats.length
      ? await api(`/api/chats/${encodeURIComponent(chats[0].id)}`)
      : await api('/api/chats', { method: 'POST' });
    if (epoch !== accountEpoch) return;
    if (!chats.length) chats = [data.chat];
    displayChat(data);
  } catch (error) {
    if (epoch === accountEpoch) {
      chatTitle.textContent = 'Чаты';
      renderChatList();
      showChatError(error);
    }
  } finally {
    if (epoch === accountEpoch) setChatBusy(false);
  }
}

async function selectChat(id) {
  if (chatBusy) return;
  if (id === activeChatId) {
    setChatsOpen(false);
    return;
  }
  const epoch = accountEpoch;
  setChatBusy(true);
  chatError.hidden = true;
  try {
    const data = await api(`/api/chats/${encodeURIComponent(id)}`);
    if (epoch === accountEpoch) displayChat(data);
  } catch (error) {
    if (epoch === accountEpoch) showChatError(error);
  } finally {
    if (epoch === accountEpoch) setChatBusy(false);
  }
}

newChatButton.addEventListener('click', async () => {
  if (chatBusy || !activeUserId) return;
  const epoch = accountEpoch;
  setChatBusy(true);
  chatError.hidden = true;
  try {
    const data = await api('/api/chats', { method: 'POST' });
    if (epoch !== accountEpoch) return;
    chats.unshift(data.chat);
    displayChat(data);
  } catch (error) {
    if (epoch === accountEpoch) showChatError(error);
  } finally {
    if (epoch === accountEpoch) {
      setChatBusy(false);
      chatInput.focus();
    }
  }
});

function addChatMessage(role, text) {
  const bubble = document.createElement('div');
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  if (role === 'bot') {
    const label = document.createElement('div');
    label.className = 'bot-label';
    label.textContent = '≋ ТИХО';
    chatMessages.append(label);
  }
  chatEmpty.hidden = true;
  chatMessages.append(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function resizeChatInput() {
  chatInput.style.height = 'auto';
  chatInput.style.height = `${chatInput.scrollHeight}px`;
}
chatInput.addEventListener('input', resizeChatInput);
window.addEventListener('resize', resizeChatInput);

chatInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});
chatForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = chatInput.value;
  if (!message.trim() || chatBusy || !activeChatId) return;
  const epoch = accountEpoch;
  const chatId = activeChatId;
  chatError.hidden = true;
  setChatBusy(true);
  try {
    const reply = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: chatId, message }),
    });
    if (epoch !== accountEpoch) return;
    for (const item of reply.messages) addChatMessage(item.role === 'assistant' ? 'bot' : 'user', item.content);
    chats = [reply.chat, ...chats.filter((item) => item.id !== chatId)];
    chatTitle.textContent = reply.chat.title;
    renderChatList();
    chatInput.value = '';
    resizeChatInput();
  } catch (error) {
    if (epoch === accountEpoch) showChatError(error);
  } finally {
    if (epoch === accountEpoch) {
      setChatBusy(false);
      chatInput.focus();
    }
  }
});

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
  if (activeUserId !== data.user_id) loadUserChats(data.user_id);
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
  if (event.key === 'Escape') setChatsOpen(false);
  if (event.key !== 'Escape' || accountPanel.hidden) return;
  if (!loginView.hidden) setLoginMode(false);
  else setPanelOpen(false);
});
initialize();
