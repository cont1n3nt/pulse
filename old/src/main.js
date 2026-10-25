
import './style.css'

const hotels = [
  { name: 'Морская тишина', stars: 4, location: 'Сочи · Мамайка', distance: '3 минуты до моря', price: 12800, rating: 9.4, reviews: 286, pool: true, beach: true, image: 'https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=900&q=85' },
  { name: 'Сад у моря', stars: 5, location: 'Сочи · Хоста', distance: '5 минут до моря', price: 14200, rating: 9.2, reviews: 412, pool: true, beach: true, image: 'https://images.unsplash.com/photo-1575429198097-0414ec08e8cd?auto=format&fit=crop&w=900&q=85' },
  { name: 'Берег и бриз', stars: 4, location: 'Сочи · Адлер', distance: '2 минуты до моря', price: 10600, rating: 9.0, reviews: 198, pool: false, beach: true, image: 'https://images.unsplash.com/photo-1570077188670-e3a8d69ac5ff?auto=format&fit=crop&w=900&q=85' },
]

let maxPrice = 15000
let poolOnly = false
let beachOnly = false
let favorites = new Set()

document.querySelector('#app').innerHTML = `
  <header class="topbar">
    <a class="logo" href="#">≋ тихо <sup>°</sup></a>
    <span class="tagline">подбор отелей с ИИ</span>
    <button class="outline" id="reset">+ Новая поездка</button>
  </header>

  <main class="layout">
    <aside class="sidebar">
      <p class="eyebrow">ВАШИ ПОЕЗДКИ</p>
      <button class="trip active">☀ Сентябрь у моря<small>Сочи · 14–19 сентября</small></button>
      <button class="trip">Выходные в Казани<small>Вчера</small></button>
      <button class="trip">Петербург вдвоём<small>21 августа</small></button>
    </aside>

    <section class="assistant panel">
      <div class="section-head">
        <div><h2>Сентябрь у моря</h2><p class="muted">Ваши пожелания — наш маршрут</p></div>
        <span>•••</span>
      </div>
      <div class="chat" id="chat">
        <div class="bubble user">Хочу в Сочи вдвоём на 14–19 сентября. Нужен тихий отель у моря, с завтраками. До 15 000 ₽ за ночь.</div>
        <div class="bot-label">✣ Тихо · ваш ИИ-консьерж</div>
        <p>Сентябрь — прекрасное время для моря. Что важнее: жить в центре или подальше от суеты? И нужен ли бассейн?</p>
        <div class="bubble user">Лучше потише, но чтобы рядом были кафе. И бассейн обязательно!</div>
        <div class="bot-label">✣ Тихо · ваш ИИ-консьерж</div>
        <p>Нашёл 3 варианта: везде есть бассейн, завтраки и море в пешей доступности. Цены — на ваши даты, за двоих.</p>
      </div>
      <div class="suggestions">
        <button class="chip prompt">Сравни пляжи</button>
        <button class="chip prompt">Покажи номера</button>
        <button class="chip prompt">Ещё дешевле</button>
      </div>
      <form id="chat-form" class="chat-form">
        <textarea id="message" placeholder="А если с видом на море?..." rows="2"></textarea>
        <button class="send" aria-label="Отправить">↑</button>
      </form>
      <p class="fineprint">ИИ помогает выбрать. Решение всегда за вами.</p>
    </section>

    <section class="results">
      <p class="eyebrow">ВАША СЛЕДУЮЩАЯ ИСТОРИЯ</p>
      <h1>Пять дней на берегу моря</h1>
      <div class="search-summary">
        <span>◎ <small>Куда</small><b>Сочи, Россия</b></span>
        <span>▦ <small>Даты</small><b>14–19 сентября · 5 ночей</b></span>
        <span>♙ <small>Кто едет</small><b>2 взрослых · 1 номер</b></span>
      </div>
      <div class="filters">
        <button class="chip selected" id="price-filter">До 15 000 ₽ / ночь</button>
        <button class="chip" id="beach-filter">≋ Рядом с морем</button>
        <button class="chip" id="pool-filter">Бассейн</button>
        <button class="chip" id="quiet-filter">Тихое место</button>
      </div>
      <div class="result-heading">
        <h3>Подобрано для вас <span id="count">3 отеля</span></h3>
        <button class="text-button" id="sort">Сначала дешевле ↕</button>
      </div>
      <div id="hotel-list" class="hotel-list"></div>
    </section>
  </main>
  <div id="toast" class="toast" role="status"></div>
`

const list = document.querySelector('#hotel-list')

function renderHotels() {
  const visible = hotels
    .filter(h => h.price <= maxPrice)
    .filter(h => !poolOnly || h.pool)
    .filter(h => !beachOnly || h.beach)
    .sort((a, b) => window.cheaper ? a.price - b.price : b.rating - a.rating)

  document.querySelector('#count').textContent = `${visible.length} отеля`
  list.innerHTML = visible.map(h => `
    <article class="hotel-card">
      <div class="hotel-image" style="background-image:url('${h.image}')">
        <span class="photo-count">▧ 12 фото</span>
      </div>
      <div class="hotel-info">
        <div class="hotel-top"><span>${h.stars} звезды · Бутик-отель</span>
          <button class="heart ${favorites.has(h.name) ? 'saved' : ''}" data-fav="${h.name}" aria-label="В избранное">${favorites.has(h.name) ? '♥' : '♡'}</button>
        </div>
        <h2>${h.name}</h2>
        <p class="muted">⌖ ${h.location} · ${h.distance}</p>
        <p class="features">${h.pool ? '✓ Бассейн' : '✓ Зона отдыха'} · Завтраки включены</p>
        <p class="features">✓ Рядом с морем · уютная атмосфера</p>
        <div class="hotel-bottom">
          <div><strong class="price">${h.price.toLocaleString('ru-RU')} ₽</strong><span class="muted"> / ночь</span>
            <p class="fineprint">${(h.price * 5).toLocaleString('ru-RU')} ₽ за 5 ночей · ориентировочная цена</p>
          </div>
          <div class="rating">Превосходно <b>${h.rating}</b><small>${h.reviews} отзывов</small></div>
        </div>
        <button class="choose" data-hotel="${h.name}">Выбрать номер →</button>
      </div>
    </article>
  `).join('') || '<p>Нет отелей по этим фильтрам. Попробуйте изменить условия.</p>'
}

function toast(message) {
  const el = document.querySelector('#toast')
  el.textContent = message
  el.classList.add('show')
  clearTimeout(window.toastTimer)
  window.toastTimer = setTimeout(() => el.classList.remove('show'), 2600)
}

renderHotels()

document.querySelector('#pool-filter').addEventListener('click', e => {
  poolOnly = !poolOnly
  e.currentTarget.classList.toggle('selected', poolOnly)
  renderHotels()
})

document.querySelector('#beach-filter').addEventListener('click', e => {
  beachOnly = !beachOnly
  e.currentTarget.classList.toggle('selected', beachOnly)
  renderHotels()
})

document.querySelector('#price-filter').addEventListener('click', () => {
  maxPrice = maxPrice === 15000 ? 12000 : 15000
  document.querySelector('#price-filter').textContent = `До ${maxPrice.toLocaleString('ru-RU')} ₽ / ночь`
  renderHotels()
})

document.querySelector('#quiet-filter').addEventListener('click', e => {
  e.currentTarget.classList.toggle('selected')
  toast('Все показанные варианты подходят для спокойного отдыха')
})

document.querySelector('#sort').addEventListener('click', () => {
  window.cheaper = !window.cheaper
  document.querySelector('#sort').textContent = window.cheaper ? 'Сначала дороже ↕' : 'Сначала дешевле ↕'
  renderHotels()
})

list.addEventListener('click', e => {
  const fav = e.target.closest('[data-fav]')
  const choose = e.target.closest('[data-hotel]')
  if (fav) {
    const name = fav.dataset.fav
    favorites.has(name) ? favorites.delete(name) : favorites.add(name)
    renderHotels()
  }
  if (choose) toast(`Вы выбрали «${choose.dataset.hotel}». Бронирование пока демонстрационное.`)
})

document.querySelectorAll('.prompt').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelector('#message').value = button.textContent
    document.querySelector('#message').focus()
  })
})

document.querySelector('#chat-form').addEventListener('submit', e => {
  e.preventDefault()
  const input = document.querySelector('#message')
  const message = input.value.trim()
  if (!message) return

  const chat = document.querySelector('#chat')
  chat.insertAdjacentHTML('beforeend', `<div class="bubble user"></div><div class="bot-label">✣ Тихо · ваш ИИ-консьерж</div><p class="reply"></p>`)
  chat.lastElementChild.textContent = 'Это демонстрационный режим. Настоящие ответы ИИ подключим через API на сервере.'
  chat.children[chat.children.length - 3].textContent = message
  input.value = ''
  chat.scrollTop = chat.scrollHeight
})

document.querySelector('#reset').addEventListener('click', () => {
  poolOnly = false
  beachOnly = false
  maxPrice = 15000
  favorites.clear()
  renderHotels()
  document.querySelectorAll('.chip').forEach(el => el.classList.remove('selected'))
  document.querySelector('#price-filter').classList.add('selected')
  document.querySelector('#price-filter').textContent = 'До 15 000 ₽ / ночь'
  toast('Начните планировать новую поездку!')
})
