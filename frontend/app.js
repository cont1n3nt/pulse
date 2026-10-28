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
const tripOrders = document.querySelector('#trip-orders');
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
  orders: document.querySelector('#trip-orders-section'),
};
let activeUserId = null;
let activeChatId = null;
let chats = [];
let chatBusy = true;
let accountEpoch = 0;
let currentCode = null;
let visible = false;
let dragOffset = null;
// что сейчас показано в панели поездки: из этого считается заголовок и счётчик на кнопке
let tripView = { packages: [], weather: null, flex: null, recommendation: null, hotels: [], flights: [], cart: null, orders: [] };

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

profileToggle.addEventListener('click', () => {
  const opening = accountPanel.hidden;
  setPanelOpen(opening);
  if (opening) loadMemory();
});

// Долговременная память агента: пользователь видит, что о нём помнят, и может стереть
async function loadMemory() {
  try {
    const data = await api('/api/memory');
    memoryText.textContent = data.text || 'Пока ничего: память появится после первой поездки.';
    forgetButton.hidden = !data.text;
  } catch {
    memoryText.textContent = 'Не удалось загрузить.';
  }
}
forgetButton.addEventListener('click', async () => {
  if (!confirm('Агент забудет город вылета, состав семьи, предпочтения и прошлые заказы. Продолжить?')) return;
  try {
    const response = await fetch('/api/memory', { method: 'DELETE', credentials: 'same-origin' });
    if (!response.ok) throw new Error();
    memoryText.textContent = 'Память очищена.';
    forgetButton.hidden = true;
  } catch {
    memoryText.textContent = 'Не удалось очистить память.';
  }
});
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
    chatList.append(button);
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
  box.append(
    el('p', 'card-title', `Заказ ${order.id} оформлен`),
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

  tripOrders.replaceChildren(...orders.map(orderContent));
  sections.orders.hidden = !orders.length;

  const empty = !packages.length && !hotels.length && !flights.length && !cart?.items.length && !orders.length;
  tripEmpty.hidden = !empty;
  const hotel = packages[0]?.hotel ?? hotels[0];
  tripTitle.textContent = orders.length && !cart?.items.length
    ? 'Поездка оформлена'
    : hotel ? `Сочи · ${shortDate(hotel.check_in)} – ${shortDate(hotel.check_out)}` : 'Пока пусто';

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
  const card = chatCard('confirm', el('p', 'card-title', 'Подтвердите оплату'), itemRows(event.items));
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

async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop();
    for (const part of parts) {
      if (part.startsWith('data: ')) onEvent(JSON.parse(part.slice(6)));
    }
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
      tripView.cart = { items: event.items, total_rub: event.total_rub, issues: event.issues, itinerary: event.itinerary };
      renderTrip();
      refreshChatPackages();
      showCartInChat(reply, tripView.cart);
    } else if (event.block === 'order') {
      tripView.orders = [...tripView.orders.filter((order) => order.id !== event.order.id), event.order];
      tripView.cart = { items: [], total_rub: 0, issues: [] };
      renderTrip();
      // корзина оформлена: её карточка в чате больше не актуальна
      chatMessages.querySelectorAll('.chat-card.cart').forEach((card) => card.remove());
      reply.addCard(chatCard('order', orderContent(event.order)));
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
  chatError.hidden = true;
  setChatBusy(true);
  const reply = addBotReply();
  try {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || 'Не удалось отправить сообщение');
    }
    await readEvents(response, (event) => {
      // пользователь переключил чат или аккаунт: этот поток больше не показываем
      if (epoch === accountEpoch && chatId === activeChatId) handleEvent(reply, event);
    });
  } catch (error) {
    if (epoch === accountEpoch) showChatError(error);
  } finally {
    reply.finish();
    if (epoch === accountEpoch) {
      setChatBusy(false);
      chatInput.focus();
    }
  }
}

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
  if (event.key === 'Escape') {
    setChatsOpen(false);
    setTripOpen(false);
  }
  if (event.key !== 'Escape' || accountPanel.hidden) return;
  if (!loginView.hidden) setLoginMode(false);
  else setPanelOpen(false);
});
initialize();
