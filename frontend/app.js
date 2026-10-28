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
const chatStop = document.querySelector('#chat-stop');
const chatReload = document.querySelector('#chat-reload');
const chatMessages = document.querySelector('#chat-messages');
const chatEmpty = document.querySelector('#chat-empty');
const chatError = document.querySelector('#chat-error');
const chatList = document.querySelector('#chat-list');
const chatTitle = document.querySelector('#chat-title');
const newChatButton = document.querySelector('#new-chat');
const chatsToggle = document.querySelector('#chats-toggle');
const tripToggle = document.querySelector('#trip-toggle');
const tripClose = document.querySelector('#trip-close');
const tripBackdrop = document.querySelector('#trip-backdrop');
const tripBadge = document.querySelector('#trip-badge');
const tripTitle = document.querySelector('#trip-title');
const tripEmpty = document.querySelector('#trip-empty');
const hotelList = document.querySelector('#hotel-list');
const hotelCount = document.querySelector('#hotel-count');
const flightList = document.querySelector('#flight-list');
const tripCart = document.querySelector('#trip-cart');
const packageList = document.querySelector('#package-list');
const packageNote = document.querySelector('#package-note');
const weatherBox = document.querySelector('#weather');
const flexHint = document.querySelector('#flex-hint');
const suggestionsBox = document.querySelector('#suggestions');
const defaultSuggestions = [...suggestionsBox.children];
const memoryText = document.querySelector('#memory-text');
const forgetButton = document.querySelector('#forget');
const sections = {
  packages: document.querySelector('#trip-packages-section'),
  hotels: document.querySelector('#trip-hotels-section'),
  flights: document.querySelector('#trip-flights-section'),
  cart: document.querySelector('#trip-cart-section'),
};
let activeUserId = null;
let activeChatId = null;
let chats = [];
let chatBusy = true;
let activeStream = null;
const API_TIMEOUT_MS = 20000;
const STREAM_IDLE_TIMEOUT_MS = 120000;
let accountEpoch = 0;
let currentCode = null;
let visible = false;
let dragOffset = null;
// что сейчас показано в панели поездки: из этого считается заголовок и счётчик на кнопке
let tripView = { packages: [], weather: null, flex: null, recommendation: null, hotels: [], flights: [], cart: null, orders: [] };

const confirmDialog = document.querySelector('#confirm-dialog');
const confirmTitle = document.querySelector('#confirm-title');
const confirmDescription = document.querySelector('#confirm-description');
const confirmAccept = document.querySelector('#confirm-accept');

// One shared modal also works above the open basket, keeping keyboard focus inside.
function confirmAction({ title, description, action, danger = false }) {
  if (confirmDialog.open) return Promise.resolve(false);
  const epoch = accountEpoch;
  const trigger = document.activeElement;
  confirmTitle.textContent = title;
  confirmDescription.textContent = description;
  confirmAccept.textContent = action;
  confirmAccept.classList.toggle('is-danger', danger);
  confirmDialog.returnValue = 'cancel';
  return new Promise(resolve => {
    confirmDialog.addEventListener('close', () => {
      const accepted = confirmDialog.returnValue === 'accept' && epoch === accountEpoch;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
      resolve(accepted);
    }, { once: true });
    confirmDialog.showModal();
    document.querySelector('#confirm-cancel').focus();
  });
}

confirmDialog.addEventListener('click', event => {
  if (event.target !== confirmDialog) return;
  const rect = confirmDialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
    confirmDialog.close('cancel');
  }
});

confirmDialog.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const buttons = [...confirmDialog.querySelectorAll('button')];
  const first = buttons[0];
  const last = buttons[buttons.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

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
  if (!open) hideAccessCode();
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
  if (active) hideAccessCode();
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

profileToggle.addEventListener('click', () => {
  const opening = accountPanel.hidden;
  setPanelOpen(opening);
  if (opening) { loadMemory(); refreshPurchases(); }
});

const accountTabs = [...document.querySelectorAll('.account-tabs [role="tab"]')];
function hideAccessCode() {
  visible = false;
  if (currentCode) codeEl.textContent = '•'.repeat(32);
  showButton.textContent = 'Показать код';
}
accountTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => {
    accountTabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
    });
    hideAccessCode();
    keepPanelInViewport();
  });
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % accountTabs.length;
    if (event.key === 'ArrowLeft') next = (index + accountTabs.length - 1) % accountTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = accountTabs.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    accountTabs[next].click();
    accountTabs[next].focus();
  });
});
const saveMemory = document.querySelector('#save-memory');
const resetMemory = document.querySelector('#reset-memory');
const retryMemory = document.querySelector('#retry-memory');
const memoryStatus = document.querySelector('#memory-status');
let savedMemory = '';
let memoryLimit = 512;
let memoryBusy = false;
let memoryRequest = 0;
function updateMemoryControls() {
  const dirty = memoryText.value !== savedMemory;
  document.querySelector('#memory-count').textContent = `${Array.from(memoryText.value).length.toLocaleString('ru-RU')} / ${memoryLimit.toLocaleString('ru-RU')}`;
  saveMemory.disabled = memoryBusy || memoryText.disabled || !dirty || Array.from(memoryText.value).length > memoryLimit;
  resetMemory.disabled = memoryBusy || !dirty;
  forgetButton.disabled = memoryBusy;
}
memoryText.addEventListener('input', () => {
  memoryStatus.textContent = 'Есть несохранённые изменения';
  updateMemoryControls();
});
resetMemory.addEventListener('click', () => {
  memoryText.value = savedMemory;
  memoryStatus.textContent = '';
  updateMemoryControls();
});
retryMemory.addEventListener('click', () => loadMemory(true));
async function loadMemory(force = false) {
  if (memoryBusy || (!force && memoryText.value !== savedMemory)) return;
  const epoch = accountEpoch;
  const request = ++memoryRequest;
  memoryText.disabled = true;
  memoryStatus.textContent = 'Загрузка…';
  retryMemory.hidden = true;
  updateMemoryControls();
  try {
    const data = await api('/api/memory');
    if (epoch !== accountEpoch || request !== memoryRequest) return;
    memoryLimit = data.max_chars || 512;
    savedMemory = data.text || '';
    memoryText.value = savedMemory;
    memoryText.disabled = false;
    forgetButton.hidden = !savedMemory;
    memoryStatus.textContent = '';
  } catch {
    if (epoch !== accountEpoch || request !== memoryRequest) return;
    memoryStatus.textContent = 'Не удалось загрузить память.';
    retryMemory.hidden = false;
  } finally { if (epoch === accountEpoch && request === memoryRequest) updateMemoryControls(); }
}
async function writeMemory(text) {
  if (memoryBusy) return;
  const epoch = accountEpoch;
  memoryBusy = true;
  memoryText.disabled = true;
  updateMemoryControls();
  try {
    const data = await api('/api/memory', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
    });
    if (epoch !== accountEpoch) return;
    memoryLimit = data.max_chars || 512;
    savedMemory = data.text || '';
    memoryText.value = savedMemory;
    forgetButton.hidden = !savedMemory;
    memoryStatus.textContent = text.trim() ? 'Сохранено' : 'Память очищена';
  } catch {
    if (epoch === accountEpoch) memoryStatus.textContent = 'Не удалось сохранить. Изменения оставлены — попробуйте ещё раз.';
  } finally {
    if (epoch === accountEpoch) { memoryBusy = false; memoryText.disabled = false; updateMemoryControls(); }
  }
}
saveMemory.addEventListener('click', () => writeMemory(memoryText.value));
forgetButton.addEventListener('click', async () => {
  if (!await confirmAction({ title: 'Очистить память?', description: 'Агент забудет предпочтения и сведения о поездках. Сами заказы останутся.', action: 'Очистить', danger: true })) return;
  await writeMemory('');
});
closePanelButton.addEventListener('click', () => setPanelOpen(false));

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const response = await fetch(path, { credentials: 'same-origin', ...options, signal: controller.signal });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || 'Не удалось выполнить запрос');
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      error = new Error('Сервер долго не отвечает. Попробуйте ещё раз.');
      error.code = 'REQUEST_TIMEOUT';
    }
    // Missing/partial responses cannot tell us whether a write reached the server.
    error.resultUnknown = !error.status && !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase());
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function setChatBusy(busy) {
  chatBusy = busy;
  newChatButton.disabled = !activeUserId || busy;
  chatSend.disabled = !activeChatId || busy;
  chatSend.hidden = Boolean(activeStream);
  chatStop.hidden = !activeStream;
  chatReload.disabled = busy;
  chatInput.disabled = !activeChatId || busy;
  chatList.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
  document.querySelectorAll('.suggestions .chip, .choose-hotel, .choose-package, .flex-button').forEach((button) => { button.disabled = !activeChatId || busy; });
}

function setChatsOpen(open) {
  document.body.classList.toggle('chats-open', open);
  chatsToggle.setAttribute('aria-expanded', String(open));
  chatsToggle.setAttribute('aria-label', open ? 'Скрыть чаты' : 'Показать чаты');
}
chatsToggle.addEventListener('click', () => setChatsOpen(!document.body.classList.contains('chats-open')));

// На широком экране поездка — третья колонка, уже — выезжающая панель
const drawerQuery = matchMedia('(max-width: 1199px)');

function setTripOpen(open) {
  const drawer = drawerQuery.matches;
  document.body.classList.toggle('trip-open', open && drawer);
  tripBackdrop.hidden = !(open && drawer);
  tripToggle.setAttribute('aria-expanded', String(open && drawer));
  if (open && drawer) tripClose.focus();
}
tripToggle.addEventListener('click', () => setTripOpen(!document.body.classList.contains('trip-open')));
tripClose.addEventListener('click', () => { setTripOpen(false); tripToggle.focus(); });
tripBackdrop.addEventListener('click', () => setTripOpen(false));
drawerQuery.addEventListener('change', () => setTripOpen(false));

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
    const row = el('div', 'chat-list-row');
    const remove = el('button', 'delete-chat', '×');
    remove.type = 'button';
    remove.title = 'Удалить чат';
    remove.setAttribute('aria-label', `Удалить чат «${item.title}»`);
    remove.disabled = chatBusy;
    remove.addEventListener('click', () => deleteChat(item));
    row.append(button, remove);
    chatList.append(row);
  }
}

// --- карточки -------------------------------------------------------------------------------

const KINDS = { hotel: 'Отель', guest_house: 'Гостевой дом', apartment: 'Апартаменты', hostel: 'Хостел', resort: 'Курорт' };
const MEALS = { none: 'без питания', breakfast: 'завтрак', half_board: 'полупансион', all_inclusive: 'всё включено' };
const SEVERITY = { blocker: 'мешает оформить', warning: 'внимание', info: 'к сведению' };
const TOOL_STATUS = {
  plan_trip: 'Собираю поездку: рейсы, отели, трансфер, погода…',
  choose_package: 'Кладу поездку в корзину и проверяю…',
  holiday_dates: 'Смотрю календарь праздников…',
  remember: 'Запоминаю…',
  update_requirements: 'Запоминаю условия…',
  search_hotels: 'Ищу отели…',
  get_hotel_details: 'Смотрю детали отеля…',
  resolve_city: 'Уточняю город…',
  search_flights: 'Ищу перелёты…',
  add_to_cart: 'Собираю поездку…',
  remove_from_cart: 'Обновляю корзину…',
  validate_trip: 'Проверяю состыковки…',
  checkout: 'Оформляю заказ…',
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const rub = (value) => (value == null ? '—' : `${Number(value).toLocaleString('ru-RU')} ₽`);

function plural(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

const shortDate = (iso) => new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
const shortTime = (iso) => new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

// --- фото отелей: подгружаются, когда карточка показалась на экране ------------------------

const photoCache = new Map();

function loadPhoto(hotelId) {
  if (!photoCache.has(hotelId)) {
    photoCache.set(hotelId, fetch(`/api/hotels/${encodeURIComponent(hotelId)}/photo`, { credentials: 'same-origin' })
      .then(async (response) => {
        if (!response.ok) return null;
        const credit = decodeURIComponent(response.headers.get('X-Photo-Credit') || '');
        const page = decodeURIComponent(response.headers.get('X-Photo-Page') || '');
        return { url: URL.createObjectURL(await response.blob()), credit, page, source: response.headers.get('X-Photo-Source') };
      })
      .catch(() => null));
  }
  return photoCache.get(hotelId);
}

const photoObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    photoObserver.unobserve(entry.target);
    fillPhoto(entry.target);
  }
}, { rootMargin: '200px' });

async function fillPhoto(box) {
  const photo = await loadPhoto(box.dataset.hotel);
  if (!photo) {
    box.classList.add('no-photo');
    return;
  }
  const img = el('img');
  img.alt = '';
  img.src = photo.url;
  box.replaceChildren(img);
  const credit = photo.page && /^https?:\/\//.test(photo.page) ? el('a', 'photo-credit') : el('span', 'photo-credit');
  credit.textContent = photo.source === 'site' ? `фото: ${photo.credit}` : `рядом с отелем · ${photo.credit}`;
  if (credit.tagName === 'A') {
    credit.href = photo.page;
    credit.target = '_blank';
    credit.rel = 'noopener noreferrer';
  }
  box.append(credit);
}

// Пока фото нет — плашка с фактами о месте: район и море. Фото подставится, если найдётся.
function photoBox(hotel) {
  const box = el('div', 'photo');
  box.dataset.hotel = hotel.id;
  const sea = hotel.distance_to_sea_m != null ? `до моря ${hotel.distance_to_sea_m} м` : 'горы';
  box.append(el('span', 'photo-placeholder', `${hotel.district} · ${sea}`));
  photoObserver.observe(box);
  return box;
}

// --- готовые поездки: перелёты + трансфер + отель ------------------------------------------

const localTime = (iso) => iso.slice(11, 16);
const localDay = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;

function trainSource(train) {
  if (train.source !== 'yandex_rasp') return 'купе, демо';
  return `${train.number}, Яндекс Расписания${train.price_is_demo ? ', цена купе — демо' : ''}`;
}

function flightLine(flight, label) {
  if (!flight) return null;
  // время рейса — местное, как в билете: берём из строки, не пересчитывая в пояс браузера
  if (flight.mode === 'train') {
    return el('p', 'pkg-flight', `${label}: поезд ${flight.from_airport} → ${flight.to_airport}, ${localDay(flight.depart_at)} ${localTime(flight.depart_at)} → ${localDay(flight.arrive_at)} ${localTime(flight.arrive_at)} · ${trainSource(flight)}`);
  }
  return el('p', 'pkg-flight', `${label}: ${localDay(flight.depart_at)} ${localTime(flight.depart_at)} → ${localTime(flight.arrive_at)} · ${flight.carrier_name}`);
}

function packageCard(pkg, compact = false) {
  const recommended = tripView.recommendation === pkg.id;
  const card = el('article', `package-card${recommended ? ' recommended' : ''}${compact ? ' compact' : ''}`);
  if (!compact) card.append(photoBox(pkg.hotel));
  const top = el('div', 'pkg-top');
  top.append(el('span', `tier ${pkg.id}`, pkg.label));
  if (recommended) top.append(el('span', 'badge', 'Советую'));
  card.append(top, el('h4', '', pkg.hotel.name), el('p', 'muted', pkg.highlights.slice(0, compact ? 3 : 5).join(' · ')));

  if (!compact) {
    for (const line of [flightLine(pkg.outbound, 'Туда'), flightLine(pkg.back, 'Обратно')]) if (line) card.append(line);
    if (pkg.transfer) {
      const start = pkg.transfer.start && pkg.transfer.start !== 'аэропорт' ? `от ${pkg.transfer.start.replace('вокзал', 'вокзала')}` : 'из аэропорта';
      card.append(el('p', 'pkg-flight', `Такси ${start} ~${pkg.transfer.minutes} мин · ${rub(pkg.transfer.price_rub)} туда-обратно`));
    }
  }
  for (const text of pkg.tradeoffs) card.append(el('p', 'tradeoff', text));
  const problems = (pkg.issues ?? []).filter((issue) => issue.severity !== 'info');
  if (problems.length && !compact) card.append(issueList(problems));
  else if (problems.length) card.append(el('p', 'warn-count', `⚠ ${problems.length} ${plural(problems.length, 'нюанс', 'нюанса', 'нюансов')}`));

  const bottom = el('div', 'hotel-bottom');
  const price = el('div');
  price.append(el('strong', 'price', rub(pkg.total_rub)));
  price.append(el('p', 'fineprint', `${rub(pkg.per_person_rub)} на человека${pkg.within_budget === false ? ' · сверх бюджета' : ''}`));
  bottom.append(price);
  const inCart = (tripView.cart?.items ?? []).some((item) => item.kind === 'hotel' && item.option_id === pkg.hotel.id);
  if (inCart) {
    bottom.append(el('span', 'in-cart', 'В корзине'));
  } else {
    const choose = el('button', 'choose-package', 'Выбрать');
    choose.type = 'button';
    choose.disabled = chatBusy || !activeChatId;
    choose.addEventListener('click', () => sendMessage(`Беру ${pkg.label}`));
    bottom.append(choose);
  }
  card.append(bottom);
  return card;
}

function weatherContent(weather) {
  const box = el('div', 'weather-row');
  for (const day of weather.days.slice(0, 7)) {
    const cell = el('div', 'weather-day');
    cell.append(el('span', 'muted', localDay(day.date)), el('strong', '', `${day.t_max}°`), el('span', 'muted', `${day.t_min}°`));
    cell.title = day.summary;
    box.append(cell);
  }
  const note = [weather.sea ? `Море ${weather.sea.t}° (${weather.sea.source})` : null, weather.source].filter(Boolean).join(' · ');
  return [box, el('p', 'fineprint', note)];
}

function flexContent(flex) {
  if (!flex) return [];
  const days = Math.abs(flex.shift_days);
  const when = `${flex.shift_days < 0 ? 'раньше' : 'позже'} на ${days} ${plural(days, 'день', 'дня', 'дней')}`;
  const button = el('button', 'flex-button', `Сдвинуть ${when}: дешевле на ${rub(flex.saving_rub)}`);
  button.type = 'button';
  button.disabled = chatBusy || !activeChatId;
  button.addEventListener('click', () => sendMessage(`Давай сдвинем поездку ${when}: с ${localDay(flex.check_in)} по ${localDay(flex.check_out)}`));
  return [button];
}

const STEP_ICONS = { flight: '✈', train: '🚆', transfer: '→', checkin: '⌂', day: '·', checkout: '↗' };

function timeline(steps) {
  const list = el('ol', 'timeline');
  for (const step of steps) {
    const row = el('li', `step ${step.kind}${step.issues.length ? ' has-issue' : ''}`);
    const when = step.kind === 'day' ? '' : `${localDay(step.at)} ${localTime(step.at)}`;
    row.append(el('span', 'step-icon', STEP_ICONS[step.kind] ?? '•'));
    const body = el('div', 'step-body');
    body.append(el('p', 'step-title', [when, step.title].filter(Boolean).join(' · ')), el('p', 'muted', step.detail));
    if (step.issues.length) body.append(issueList(step.issues));
    row.append(body);
    list.append(row);
  }
  return list;
}

function hotelCard(hotel, cartIds) {
  const card = el('article', 'hotel-card');
  card.append(photoBox(hotel));
  const top = el('div', 'hotel-top');
  top.append(el('span', '', [hotel.stars ? `${hotel.stars}★` : '', KINDS[hotel.kind] ?? hotel.kind].filter(Boolean).join(' · ')));
  if (hotel.source === 'osm') top.append(el('span', 'tag', 'OSM'));
  card.append(top, el('h4', '', hotel.name));

  const place = [hotel.district];
  place.push(hotel.distance_to_sea_m != null ? `до моря ${hotel.distance_to_sea_m} м` : 'в горах');
  card.append(el('p', 'muted', place.join(' · ')));
  if (hotel.address) card.append(el('p', 'address', hotel.address));
  if (hotel.pros?.length) card.append(el('p', 'pros', `+ ${hotel.pros.slice(0, 3).join(' · ')}`));
  if (hotel.cons?.length) card.append(el('p', 'cons', `− ${hotel.cons.slice(0, 2).join(' · ')}`));
  const terms = [MEALS[hotel.meal] ?? hotel.meal, hotel.free_cancellation ? 'бесплатная отмена' : 'без бесплатной отмены'].filter(Boolean);
  card.append(el('p', 'features', terms.join(' · ')));

  // объект реальный: его можно открыть на карте и на сайте отеля
  const links = el('p', 'hotel-links');
  for (const [href, label] of [[hotel.osm_url, 'На карте'], [hotel.website, 'Сайт отеля']]) {
    if (!href || !/^https?:\/\//.test(href)) continue;
    const link = el('a', '', label);
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    links.append(link);
  }
  if (links.childElementCount) card.append(links);

  const bottom = el('div', 'hotel-bottom');
  const price = el('div');
  price.append(el('strong', 'price', rub(hotel.price_per_night_rub)), el('span', 'muted', ' / ночь'));
  if (hotel.is_demo) price.append(el('span', 'demo-mark', ' · цена демо'));
  if (hotel.total_price_rub != null) {
    price.append(el('p', 'fineprint', `${rub(hotel.total_price_rub)} за ${hotel.nights} ${plural(hotel.nights, 'ночь', 'ночи', 'ночей')}`));
  }
  bottom.append(price);
  if (cartIds.has(hotel.id)) {
    bottom.append(el('span', 'in-cart', 'В корзине'));
  } else {
    const choose = el('button', 'choose-hotel', 'Выбрать');
    choose.type = 'button';
    choose.disabled = chatBusy || !activeChatId;
    // выбор идёт через агента: он положит отель в корзину и проверит поездку
    choose.addEventListener('click', () => sendMessage(`Беру «${hotel.name}»`));
    bottom.append(choose);
  }
  card.append(bottom);
  return card;
}

function issueList(issues) {
  const list = el('ul', 'issues');
  for (const issue of issues) {
    const row = el('li', `issue ${issue.severity}`);
    row.append(el('b', '', `${SEVERITY[issue.severity] ?? issue.severity}: `), document.createTextNode(issue.message));
    list.append(row);
  }
  return list;
}

function itemRows(items) {
  const list = el('ul', 'cart-items');
  for (const item of items) {
    const row = el('li');
    row.append(el('span', '', item.title), el('strong', '', rub(item.price_rub)));
    list.append(row);
  }
  return list;
}

// В панели — с маршрутом по часам, где проблемы стоят у своего шага; в чате — коротко
function cartContent(cart, withTimeline = false) {
  const box = el('div', 'cart-box');
  if (cart.replaces) box.append(el('p', 'card-title', `Перенос заказа ${cart.replaces}`),
    el('p', 'muted', 'Прежний заказ отменится после подтверждения оплаты нового.'));
  if (cart.extends) box.append(el('p', 'card-title', `Дополнение к заказу ${cart.extends}`));
  if (!cart.items.length) {
    box.append(el('p', 'muted', 'Корзина пуста'));
  } else {
    box.append(itemRows(cart.items), el('p', 'cart-total', `Итого ${rub(cart.total_rub)} · ориентировочно`));
  }
  let issues = cart.issues ?? [];
  if (withTimeline && cart.itinerary?.length) {
    const shown = new Set(cart.itinerary.flatMap((step) => step.issues.map((issue) => issue.code)));
    box.append(el('p', 'card-title timeline-title', 'Маршрут'), timeline(cart.itinerary));
    issues = issues.filter((issue) => !shown.has(issue.code));
  }
  if (issues.length) box.append(issueList(issues));
  return box;
}

function orderContent(order) {
  const box = el('div', 'order-box');
  const cancelled = order.status === 'cancelled';
  box.classList.toggle('cancelled', cancelled);
  box.append(
    el('p', 'card-title', cancelled ? `Заказ ${order.id} отменён (перенесён)` : `Заказ ${order.id} оформлен`),
    itemRows(order.items),
    el('p', 'cart-total', `Итого ${rub(order.total_rub)} · оплата имитирована`),
  );
  return box;
}

// --- панель поездки -------------------------------------------------------------------------

function renderTrip() {
  const { packages, weather, flex, hotels, flights, cart, orders } = tripView;
  const cartIds = new Set((cart?.items ?? []).map((item) => item.option_id));

  packageList.replaceChildren(...packages.map((pkg) => packageCard(pkg)));
  const byTrain = packages.some((pkg) => pkg.outbound?.mode === 'train');
  packageNote.textContent = packages.length ? `${byTrain ? 'поезд' : 'перелёт'} + трансфер + отель` : '';
  weatherBox.replaceChildren(...(weather ? weatherContent(weather) : []));
  weatherBox.hidden = !weather || !packages.length;
  flexHint.replaceChildren(...flexContent(flex));
  sections.packages.hidden = !packages.length;

  hotelList.replaceChildren(...hotels.map((hotel) => hotelCard(hotel, cartIds)));
  hotelCount.textContent = hotels.length ? `${hotels.length} ${plural(hotels.length, 'отель', 'отеля', 'отелей')}` : '';
  sections.hotels.hidden = !hotels.length;

  flightList.replaceChildren(...flights.map((flight) => {
    const row = el('div', 'flight-row');
    row.append(
      el('span', '', `${flight.carrier_name} ${flight.number}`),
      el('span', 'muted', `${flight.from_airport} → ${flight.to_airport} · ${shortTime(flight.depart_at)}`),
      el('strong', '', rub(flight.price_rub)),
    );
    return row;
  }));
  sections.flights.hidden = !flights.length;

  tripCart.replaceChildren(...(cart?.items.length ? [cartContent(cart, true)] : []));
  sections.cart.hidden = !cart?.items.length;

  document.querySelector('#trip-checkout').hidden = !cart?.items.length;
  document.querySelector('#trip-checkout-total').textContent = rub(cart?.total_rub ?? 0);

  const empty = !packages.length && !hotels.length && !flights.length && !cart?.items.length && !orders.length;
  tripEmpty.hidden = !empty;
  const hotel = packages[0]?.hotel ?? hotels[0];
  tripTitle.textContent = orders.length && !cart?.items.length
    ? 'Поездка оформлена'
    : hotel ? `${locationCity} · ${shortDate(hotel.check_in)} – ${shortDate(hotel.check_out)}` : 'Пока пусто';

  const count = cart?.items.length || packages.length || hotels.length;
  tripBadge.textContent = count ? String(count) : '';
  tripBadge.hidden = !count;
  tripBadge.classList.toggle('filled', Boolean(cart?.items.length));
}

function resetTrip(trip) {
  tripView = {
    packages: trip?.packages ?? [],
    weather: trip?.weather ?? null,
    flex: null,
    recommendation: null,
    hotels: trip?.hotels ?? [],
    flights: trip?.flights ?? [],
    cart: trip?.cart ?? null,
    orders: trip?.orders ?? [],
  };
  renderTrip();
}

// --- сообщения ------------------------------------------------------------------------------

function clearChat() {
  activeChatId = null;
  chatMessages.replaceChildren(chatEmpty);
  chatEmpty.hidden = false;
  chatInput.value = '';
  resizeChatInput();
  chatError.hidden = true;
  chatReload.hidden = true;
  resetTrip(null);
  setSuggestions(null);
}

function scrollChat() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addChatMessage(role, text) {
  if (role === 'bot') chatMessages.append(el('div', 'bot-label', '≋ ТИХО'));
  const bubble = el('div', `bubble ${role}`, text);
  chatEmpty.hidden = true;
  chatMessages.append(bubble);
  scrollChat();
  return bubble;
}

// Ответ агента: статус работы, текст по мере генерации и карточки под ним
function addBotReply() {
  chatEmpty.hidden = true;
  const label = el('div', 'bot-label', '≋ ТИХО');
  const status = el('div', 'reasoning');
  status.append(el('span', 'spinner'), el('span', 'status-text', 'Думаю…'));
  const bubble = el('div', 'bubble bot');
  bubble.hidden = true;
  const cards = el('div', 'bot-cards');
  chatMessages.append(label, status, bubble, cards);
  scrollChat();
  return {
    label, status, bubble, cards,
    setStatus(text) { status.querySelector('.status-text').textContent = text; },
    append(token) {
      bubble.hidden = false;
      bubble.textContent += token;
      scrollChat();
    },
    addCard(card) {
      cards.append(card);
      scrollChat();
    },
    finish() {
      status.remove();
      if (!bubble.textContent && !cards.childElementCount) label.remove();
      if (!bubble.textContent) bubble.remove();
      if (!cards.childElementCount) cards.remove();
    },
  };
}

function chatCard(className, ...children) {
  const card = el('div', `chat-card ${className}`);
  card.append(...children);
  return card;
}

// Корзина в чате одна: новая версия заменяет прежнюю карточку
function showCartInChat(reply, cart) {
  chatMessages.querySelectorAll('.chat-card.cart').forEach((card) => card.remove());
  const title = el('p', 'card-title', 'Ваша поездка');
  reply.addCard(chatCard('cart', title, cartContent(cart)));
}

// Пакеты в чате — короткими карточками в ряд; подробности, фото и маршрут — в панели поездки
function showPackagesInChat(reply, packages) {
  const row = el('div', 'package-row');
  row.append(...packages.map((pkg) => packageCard(pkg, true)));
  reply.addCard(row);
}

// Рекомендация приходит в конце ответа: перерисовываем пакеты этого ответа с отметкой «Советую»
function markRecommended(reply) {
  const row = reply.cards.querySelector('.package-row');
  if (row) row.replaceChildren(...tripView.packages.map((pkg) => packageCard(pkg, true)));
}

// Корзина изменилась: в последней подборке в чате выбранный пакет помечается «В корзине»
function refreshChatPackages() {
  const rows = chatMessages.querySelectorAll('.package-row');
  const last = rows[rows.length - 1];
  if (last) last.replaceChildren(...tripView.packages.map((pkg) => packageCard(pkg, true)));
}

// Подсказки под полем ввода: следующие шаги от агента, а до первого ответа — примеры
function setSuggestions(items) {
  if (!items?.length) {
    suggestionsBox.replaceChildren(...defaultSuggestions);
    return;
  }
  suggestionsBox.replaceChildren(...items.map((text) => {
    const chip = el('button', 'chip', text);
    chip.type = 'button';
    chip.dataset.prompt = text;
    chip.disabled = chatBusy || !activeChatId;
    chip.addEventListener('click', () => sendMessage(text));
    return chip;
  }));
}

function variantsHint(reply, count) {
  // на узком экране варианты в выезжающей панели: подсказка открывает её
  const button = el('button', 'variants-hint', `Показать ${count} ${plural(count, 'вариант', 'варианта', 'вариантов')} →`);
  button.type = 'button';
  button.addEventListener('click', () => setTripOpen(true));
  reply.addCard(button);
}

function showConfirm(container, event) {
  const actions = el('div', 'confirm-actions');
  const pay = el('button', 'pay', `Оплатить ${rub(event.total_rub)}`);
  const cancel = el('button', 'cancel', 'Отмена');
  pay.type = cancel.type = 'button';
  const title = event.replaces ? `Подтвердите перенос заказа ${event.replaces}`
    : event.extends ? `Подтвердите дополнение к заказу ${event.extends}` : 'Подтвердите оплату';
  const card = chatCard('confirm', el('p', 'card-title', title), itemRows(event.items));
  if (event.replaces) card.append(el('p', 'muted', `Заказ ${event.replaces} отменится после оплаты нового`));
  if (event.issues?.length) card.append(issueList(event.issues));
  const decide = (approved) => {
    if (chatBusy) return;
    card.classList.add('decided');
    actions.replaceChildren(el('p', 'muted', approved ? 'Оплата подтверждена' : 'Оплата отменена'));
    streamAgent(`/api/chats/${encodeURIComponent(activeChatId)}/checkout`, { approved });
  };
  pay.addEventListener('click', () => decide(true));
  cancel.addEventListener('click', () => decide(false));
  actions.append(pay, cancel);
  card.append(actions, el('p', 'fineprint', 'Это демо: деньги не списываются.'));
  container(card);
}

// Новое сообщение вместо кнопки бэк считает отказом от оплаты: карточка гаснет
function closePendingConfirms() {
  for (const card of chatMessages.querySelectorAll('.chat-card.confirm:not(.decided)')) {
    card.classList.add('decided');
    card.querySelector('.confirm-actions')?.replaceChildren(el('p', 'muted', 'Оплата не подтверждена'));
  }
}

async function readEvents(response, onEvent, onProgress) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error('Ответ прервался до завершения. Обновите чат, чтобы проверить результат.');
      onProgress();
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop();
      for (const part of parts) {
        if (!part.startsWith('data: ')) continue;
        const event = JSON.parse(part.slice(6));
        onEvent(event);
        if (event.type === 'done') return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function handleEvent(reply, event) {
  if (event.type === 'token') {
    reply.status.hidden = true;
    reply.append(event.text);
  } else if (event.type === 'tool') {
    reply.status.hidden = false;
    reply.setStatus(TOOL_STATUS[event.name] ?? 'Работаю…');
  } else if (event.type === 'block') {
    if (event.block === 'packages') {
      Object.assign(tripView, { packages: event.items, weather: event.weather, flex: event.flex, recommendation: null });
      renderTrip();
      showPackagesInChat(reply, event.items);
    } else if (event.block === 'reply') {
      tripView.recommendation = event.recommendation;
      renderTrip();
      markRecommended(reply);
      setSuggestions(event.suggestions);
    } else if (event.block === 'hotels') {
      tripView.hotels = event.items;
      renderTrip();
      // пакеты уже показаны в чате: отдельная подсказка про отели не нужна
      if (!reply.cards.querySelector('.package-row')) variantsHint(reply, event.items.length);
    } else if (event.block === 'flights') {
      tripView.flights = event.items;
      renderTrip();
    } else if (event.block === 'cart') {
      tripView.cart = { items: event.items, total_rub: event.total_rub, issues: event.issues,
        itinerary: event.itinerary, replaces: event.replaces, extends: event.extends };
      renderTrip();
      refreshChatPackages();
      showCartInChat(reply, tripView.cart);
    } else if (event.block === 'order') {
      tripView.orders = [...tripView.orders.filter((order) => order.id !== event.order.id), event.order]
        .map((order) => (order.id === event.cancelled ? { ...order, status: 'cancelled' } : order));
      tripView.cart = { items: [], total_rub: 0, issues: [] };
      renderTrip();
      // корзина оформлена: её карточка в чате больше не актуальна
      chatMessages.querySelectorAll('.chat-card.cart').forEach((card) => card.remove());
      const link = actionButton('Заказ оформлен · Открыть профиль', () => { setPanelOpen(true); refreshPurchases(); });
      reply.addCard(link);
    }
  } else if (event.type === 'confirm') {
    showConfirm((card) => reply.addCard(card), event);
  } else if (event.type === 'chat') {
    chats = [event.chat, ...chats.filter((item) => item.id !== event.chat.id)];
    if (event.chat.id === activeChatId) chatTitle.textContent = event.chat.title;
    renderChatList();
  } else if (event.type === 'error') {
    throw new Error(event.detail);
  }
}

// Один ход агента: запрос, поток SSE, текст и карточки в чате
async function streamAgent(url, payload) {
  if (chatBusy || !activeChatId) return;
  const epoch = accountEpoch;
  const chatId = activeChatId;
  const stream = { controller: new AbortController(), timedOut: false };
  activeStream = stream;
  const isCurrent = () => activeStream === stream && epoch === accountEpoch && chatId === activeChatId;
  let timeout;
  const resetTimeout = () => {
    clearTimeout(timeout);
    if (!stream.controller.signal.aborted) timeout = setTimeout(() => {
      stream.timedOut = true;
      stream.controller.abort();
    }, STREAM_IDLE_TIMEOUT_MS);
  };
  resetTimeout();
  chatError.hidden = true;
  chatReload.hidden = true;
  setChatBusy(true);
  const reply = addBotReply();
  try {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: stream.controller.signal,
    });
    resetTimeout();
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.detail || 'Не удалось отправить сообщение');
    }
    await readEvents(response, (event) => {
      if (isCurrent() && !stream.controller.signal.aborted) handleEvent(reply, event);
    }, resetTimeout);
  } catch (error) {
    if (isCurrent()) {
      if (stream.controller.signal.aborted) error = new Error(stream.timedOut
        ? 'Две минуты нет новых данных. Ожидание остановлено. Обновите чат, чтобы проверить результат.'
        : 'Ожидание остановлено. Запрос может ещё выполняться. Обновите чат перед повторной отправкой.');
      showChatError(error);
      chatReload.hidden = false;
    }
  } finally {
    clearTimeout(timeout);
    reply.finish();
    if (isCurrent()) {
      activeStream = null;
      setChatBusy(false);
      chatInput.focus();
      refreshPurchases();
    }
  }
}

chatStop.addEventListener('click', () => activeStream?.controller.abort());
chatReload.addEventListener('click', async () => {
  if (chatBusy || !activeChatId) return;
  const epoch = accountEpoch;
  const chatId = activeChatId;
  setChatBusy(true);
  try {
    const data = await api(`/api/chats/${encodeURIComponent(chatId)}`);
    if (epoch === accountEpoch && chatId === activeChatId) displayChat(data);
  } catch (error) {
    if (epoch === accountEpoch && chatId === activeChatId) showChatError(error);
  } finally {
    if (epoch === accountEpoch && chatId === activeChatId) {
      setChatBusy(false);
      refreshPurchases();
    }
  }
});

function sendMessage(text) {
  const message = text.trim();
  if (!message || chatBusy || !activeChatId) return;
  closePendingConfirms();
  addChatMessage('user', message);
  streamAgent('/api/chat', { session_id: activeChatId, message });
}

function displayChat(data) {
  clearChat();
  activeChatId = data.chat.id;
  chatTitle.textContent = data.chat.title;
  for (const message of data.messages) {
    addChatMessage(message.role === 'assistant' ? 'bot' : 'user', message.content);
  }
  resetTrip(data.trip);
  if (data.trip?.cart?.items.length) {
    chatMessages.append(chatCard('cart', el('p', 'card-title', 'Ваша поездка'), cartContent(data.trip.cart)));
  }
  // заказ ждал подтверждения, когда страницу закрыли: возвращаем кнопку оплаты
  if (data.trip?.confirm) showConfirm((card) => chatMessages.append(card), data.trip.confirm);
  scrollChat();
  renderChatList();
  setChatsOpen(false);
}

async function loadUserChats(userId) {
  activeStream?.controller.abort();
  activeStream = null;
  activeUserId = userId;
  const epoch = ++accountEpoch;
  ++memoryRequest;
  memoryBusy = false;
  savedMemory = '';
  memoryText.value = '';
  memoryText.disabled = true;
  forgetButton.hidden = true;
  memoryStatus.textContent = '';
  updateMemoryControls();
  loadMemory(true);
  chats = [];
  purchases = { carts: [], orders: [] };
  purchasesError = '';
  setBasketActionMessage('');
  paintPurchases();
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
    refreshPurchases();
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
chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  if (!chatInput.value.trim() || chatBusy || !activeChatId) return;
  const message = chatInput.value;
  chatInput.value = '';
  resizeChatInput();
  sendMessage(message);
});
document.querySelectorAll('.suggestions .chip').forEach((chip) => {
  chip.addEventListener('click', () => sendMessage(chip.dataset.prompt));
});

// Локация агента (backend/seed/<id>/location.json): подпись поездки и подсказки пустого чата.
// В разметке — подсказки для Сочи на случай, если запрос не прошёл.
let locationCity = 'Сочи';

async function loadLocation() {
  try {
    const place = await api('/api/location');
    locationCity = place.city;
    if (!place.examples.length) return;
    const checkout = defaultSuggestions.find((chip) => chip.dataset.prompt === 'Оформляй');
    const chips = place.examples.map(({ label, prompt }) => {
      const chip = el('button', 'chip', label);
      chip.type = 'button';
      chip.dataset.prompt = prompt;
      chip.addEventListener('click', () => sendMessage(prompt));
      return chip;
    });
    const showingDefaults = suggestionsBox.firstElementChild === defaultSuggestions[0];
    defaultSuggestions.splice(0, defaultSuggestions.length, ...chips, ...(checkout ? [checkout] : []));
    if (showingDefaults) suggestionsBox.replaceChildren(...defaultSuggestions);
    chatEmpty.textContent = 'Опишите поездку своими словами: откуда, когда, с кем и на какой бюджет. '
      + `Я соберу дорогу${place.trains ? ' (самолёт или поезд)' : ''}, трансфер и отель ${place.title === place.city ? '' : `(${place.title}) `}`
      + `и проверю, что всё стыкуется. Например: «${place.examples[0].prompt}».`;
  } catch {
    // подсказки из разметки остаются
  }
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
  if (activeUserId !== data.user_id) loadUserChats(data.user_id);
}

async function initialize() {
  try {
    render(await api('/api/auth/me'));
  } catch (error) {
    if (error.status !== 401) {
      chatTitle.textContent = 'Не удалось загрузить чаты';
      showChatError(error);
      setPanelOpen(true);
      messageEl.textContent = error.message;
      return;
    }
    try {
      render(await api('/api/auth/guest', { method: 'POST' }));
    } catch (guestError) {
      chatTitle.textContent = 'Не удалось загрузить чаты';
      showChatError(guestError);
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
  if (!await confirmAction({ title: 'Перевыпустить код?', description: 'Старый код перестанет работать.', action: 'Перевыпустить' })) return;
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
  if (document.querySelector('dialog[open]')) return;
  if (event.key === 'Escape') {
    setChatsOpen(false);
    setTripOpen(false);
  }
  if (event.key !== 'Escape' || accountPanel.hidden) return;
  if (!loginView.hidden) setLoginMode(false);
  else setPanelOpen(false);
});

const basketDialog = document.querySelector('#basket-dialog');
const basketList = document.querySelector('#basket-list');
const basketStatus = document.querySelector('#basket-status');
let purchases = { carts: [], orders: [] };
let purchasesRequest = 0;
let purchaseBusy = false;
let basketActionMessage = '';
let purchasesError = '';

function renderBasketStatus() {
  // A refresh must not erase a payment result or a rejected cart action.
  basketStatus.textContent = [basketActionMessage, purchasesError].filter(Boolean).join(' ');
}

function setBasketActionMessage(message) {
  basketActionMessage = message;
  renderBasketStatus();
}

function actionButton(text, action, className = 'cancel') {
  const button = el('button', className, text);
  button.type = 'button';
  button.addEventListener('click', action);
  return button;
}

async function deleteChat(item) {
  if (chatBusy) return;
  if (!await confirmAction({ title: `Удалить чат «${item.title}»?`, description: 'Поездки и заказы сохранятся.', action: 'Удалить', danger: true }) || chatBusy) return;
  const epoch = accountEpoch;
  setChatBusy(true);
  try {
    await api(`/api/chats/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
    if (epoch !== accountEpoch) return;
    chats = chats.filter(chat => chat.id !== item.id);
    if (item.id === activeChatId) {
      clearChat();
      const data = chats.length ? await api(`/api/chats/${encodeURIComponent(chats[0].id)}`)
        : await api('/api/chats', { method: 'POST' });
      if (epoch !== accountEpoch) return;
      if (!chats.length) chats = [data.chat];
      displayChat(data);
    }
    renderChatList();
    refreshPurchases();
  } catch (error) { if (epoch === accountEpoch) showChatError(error); }
  finally { if (epoch === accountEpoch) setChatBusy(false); }
}

async function refreshPurchases() {
  if (!activeUserId) return;
  const epoch = accountEpoch;
  const request = ++purchasesRequest;
  try {
    const data = await api('/api/purchases');
    if (epoch !== accountEpoch || request !== purchasesRequest) return;
    purchases = data;
    purchasesError = '';
    renderBasketStatus();
    paintPurchases();
  } catch (error) {
    if (epoch !== accountEpoch || request !== purchasesRequest) return;
    purchasesError = error.message;
    renderBasketStatus();
    document.querySelector('#profile-orders').replaceChildren(
      el('p', 'form-error', 'Не удалось загрузить заказы.'), actionButton('Повторить', refreshPurchases));
  }
}

function paintPurchases() {
  const badge = document.querySelector('#basket-count');
  badge.textContent = purchases.carts.length;
  badge.hidden = !purchases.carts.length;
  document.querySelector('#basket-toggle').setAttribute('aria-label', `Корзина: ${purchases.carts.length} поездок`);
  basketList.replaceChildren();
  if (!purchases.carts.length) basketList.append(el('p', 'basket-empty', 'Здесь пока пусто. Выберите понравившийся вариант в чате — он появится в корзине.'));
  for (const cart of purchases.carts) {
    const card = el('article', 'purchase-card');
    card.append(el('h3', '', cart.title), cartContent(cart));
    const actions = el('div', 'confirm-actions');
    const pay = actionButton(`Оплатить ${rub(cart.total_rub)}`, () => purchaseAction(cart, true), 'pay');
    pay.disabled = purchaseBusy || chatBusy || cart.issues.some(i => i.severity === 'blocker');
    const remove = actionButton('Удалить', () => purchaseAction(cart, false), 'cancel danger');
    remove.disabled = purchaseBusy || chatBusy;
    actions.append(pay, remove);
    if (!cart.archived) {
      const open = actionButton('Перейти в чат', () => { basketDialog.close(); selectChat(cart.chat_id); }, 'text-link');
      open.disabled = purchaseBusy || chatBusy;
      actions.append(open);
    }
    card.append(actions);
    basketList.append(card);
  }
  const history = document.querySelector('#profile-orders');
  history.replaceChildren();
  if (!purchases.orders.length) history.append(el('p', 'muted', 'Пока нет оформленных заказов.'));
  for (const order of purchases.orders) {
    const card = orderContent(order);
    card.append(actionButton('Удалить из истории', async () => {
      if (!await confirmAction({ title: 'Удалить заказ из истории?', description: 'Сам заказ останется действующим.', action: 'Удалить', danger: true })) return;
      try {
        await api(`/api/orders/${encodeURIComponent(order.id)}`, { method: 'DELETE' });
        await refreshPurchases();
      } catch (error) { messageEl.textContent = error.message; }
    }, 'secondary'));
    history.append(card);
  }
}

async function purchaseAction(cart, pay) {
  if (purchaseBusy || chatBusy) return;
  const paymentPrompt = cart.replaces
    ? `Перенести заказ ${cart.replaces} за ${rub(cart.total_rub)}?`
    : cart.extends ? `Оформить дополнение к заказу ${cart.extends} за ${rub(cart.total_rub)}?`
      : `Оформить поездку за ${rub(cart.total_rub)}?`;
  if (!await confirmAction({
    title: pay ? paymentPrompt : 'Удалить поездку из корзины?',
    description: pay ? `${cart.replaces ? 'Прежний заказ отменится. ' : ''}Демо-оплата, деньги не спишутся.` : 'Поездка исчезнет из корзины.',
    action: pay ? 'Оформить' : 'Удалить',
    danger: !pay,
  }) || purchaseBusy || chatBusy) return;
  const epoch = accountEpoch;
  purchaseBusy = true;
  setChatBusy(true);
  paintPurchases();
  setBasketActionMessage(pay ? 'Оформляем поездку…' : 'Удаляем…');
  try {
    const data = await api(`/api/purchases/${encodeURIComponent(cart.chat_id)}${pay ? '/pay' : ''}`, {
      method: pay ? 'POST' : 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: cart.version }),
    });
    if (epoch !== accountEpoch) return;
    setBasketActionMessage(pay ? `Заказ ${data.order.id} оформлен и сохранён в профиле. Деньги не списаны.` : 'Поездка удалена из корзины.');
    if (activeChatId === cart.chat_id) {
      try {
        const chat = await api(`/api/chats/${encodeURIComponent(cart.chat_id)}`);
        if (epoch === accountEpoch && activeChatId === cart.chat_id) displayChat(chat);
      } catch {
        if (epoch === accountEpoch && activeChatId === cart.chat_id) {
          showChatError(new Error('Операция выполнена, но не удалось обновить чат. Обновите его ещё раз.'));
          chatReload.hidden = false;
        }
      }
    }
  } catch (error) {
    if (epoch === accountEpoch) {
      setBasketActionMessage(error.resultUnknown
        ? 'Не удалось получить результат операции. Проверьте корзину и заказы перед повтором.'
        : error.message);
      if (error.resultUnknown && activeChatId === cart.chat_id) chatReload.hidden = false;
    }
  } finally {
    purchaseBusy = false;
    if (epoch === accountEpoch) { setChatBusy(false); await refreshPurchases(); }
  }
}

function openBasket() {
  setPanelOpen(false);
  setTripOpen(false);
  setBasketActionMessage('');
  paintPurchases();
  basketDialog.showModal();
  refreshPurchases();
}
document.querySelector('#basket-toggle').addEventListener('click', openBasket);
document.querySelector('#open-basket').addEventListener('click', openBasket);
document.querySelector('#basket-close').addEventListener('click', () => basketDialog.close());
basketDialog.addEventListener('click', event => { if (event.target === basketDialog && event.clientX < basketDialog.getBoundingClientRect().left) basketDialog.close(); });
loadLocation();
initialize();
