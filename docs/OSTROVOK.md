# Как подключить Pulse к API Островка

Агент, проверки и интерфейс не знают, откуда пришли отели, билеты и заказы. Они работают с моделями
из `backend/agent/contracts.py` и вызывают источники только через реестр `backend/agent/providers.py`.
Чтобы перевести Pulse на инвентарь Островка, нужно написать классы с методами из протоколов
и выбрать их переменной окружения. Код агента, промпт, проверки и фронт не меняются.

```
PULSE_PROVIDERS=demo                        # сейчас: OSM + демо-цены, Aviasales, заказы в SQLite
PULSE_PROVIDERS=ostrovok.pulse:providers    # функция без аргументов, возвращает providers.Providers
```

Ошибка в адаптере видна при старте: реестр проверяет, что каждый провайдер реализует свой протокол.
Что ядро действительно работает на чужом источнике, показывает `tests/test_providers.py`. В нём пакеты,
проверка поездки и перенос заказа идут на «партнёрском» API в памяти: пять отелей со своими часами
заселения и своё бронирование.

## Протоколы и что за ними стоит у Островка

Ниже — соответствие шагам ETG API v3 (B2B API Островка/RateHawk) по
[документации](https://docs.emergingtravel.com/docs). Названия методов и полей нужно сверить
с актуальной версией при подключении.

| Протокол и метод | Когда вызывает Pulse | ETG API v3 |
|---|---|---|
| `HotelProvider.search(check_in, check_out, district, …)` | подбор пакетов и отелей | `search/serp/region/` (весь регион) или `search/serp/hotels/` (список id), затем фильтр по району и удобствам |
| `HotelProvider.get(hotel_id)` | карточка отеля, фото | статический контент отеля (дамп или `hotel/static/`) |
| `HotelProvider.quote(hotel_id, check_in, check_out)` | проверка корзины перед оплатой, перенос заказа, «даты подешевле» | `search/hp/` (тарифы одного отеля); перед оплатой — `hotel/prebook/` |
| `BookingGateway.create(session_id, version, items, total_rub)` | только после кнопки «Оплатить» (`interrupt` в `checkout`) | `hotel/order/booking/form/` → `hotel/order/booking/finish/` → опрос `…/finish/status/` |
| `BookingGateway.cancel(order_id)` | перенос: старый заказ отменяется после оплаты нового | `hotel/order/cancel/` |
| `BookingGateway.get(order_id)` | профиль, история заказов | данные заказа партнёра |
| `TransferProvider.transfer / transfer_minutes / for_guests` | пакет «дорога + трансфер + отель» | партнёр трансферов; время дороги — уже из OSRM |
| `FlightProvider.cheapest_on / prices_around / fetch_flights` | перелёты в пакете и гибкие даты | партнёрский API авиабилетов |

## Поля `HotelOption`

| Поле | Откуда у Островка |
|---|---|
| `id`, `name`, `kind`, `stars`, `address`, `lat`, `lon` | статический контент отеля |
| `district` | регион или район из справочника регионов; районы локации — в `location.json` |
| `amenities` (`wifi`, `pool`, `parking`, `kids`, `pets`, `breakfast`, `beach_line`, `spa`) | удобства из статического контента; нет данных — `None`, и проверка скажет «уточните у отеля» |
| `check_in_time`, `check_out_time` | часы заселения и выезда отеля; без них берутся типовые из `location.json` |
| `price_per_night_rub`, `total_price_rub`, `available` | тариф из `search/hp/`, перед оплатой — подтверждение `prebook` |
| `meal` | питание тарифа (`meal` или `meal_data`) |
| `free_cancellation` | есть ли бесплатная отмена тарифа (`cancellation_penalties.free_cancellation_before`) |
| `is_demo` | `False`: интерфейс и ответы агента перестают помечать цену как демо |

Тариф (`book_hash`) нужно хранить в `snapshot` позиции корзины. `CartItem.snapshot` сохраняется
в состоянии поездки целиком, поэтому `BookingGateway.create` получит его вместе с позицией.

## Что остаётся тем же

- **Суммы и проверки.** Итог корзины, «прилёт после заезда», «ночь без жилья», «хостел с детьми»,
  бюджет считает `trip.py` по моделям, а не модель.
- **Точка оплаты.** Заказ создаётся только в `checkout` после `interrupt` и кнопки «Оплатить»
  (или из корзины профиля, тоже по кнопке). Повтор той же версии корзины возвращает тот же заказ.
- **Перенос.** Новая корзина на те же отель, дорогу и трансфер с разницей в рублях. Старый заказ
  отменяется только после оплаты нового.
- **Локация.** `backend/seed/<id>/location.json`: районы, аэропорт, часовой пояс, море, сезонность.
  Для регионов Островка его можно собрать из справочника регионов.

## Что ещё не за протоколом

- **Поезда.** `adapters/trains.py` (Яндекс Расписания) подключён напрямую. Это отдельный вид дороги
  поверх тех же контрактов (`FlightOption` с `mode="train"`, трансфер от вокзала). Следующий шаг —
  такой же `RailProvider`.
- **Погода, праздники, фото.** Справочные данные, а не инвентарь: `adapters/weather.py`,
  `adapters/holidays.py`, `adapters/photos.py`.
- **Состояние диалогов и память.** `SqliteSaver` и хранилище LangGraph. Для нескольких воркеров —
  `PostgresSaver` и Postgres той же LangGraph.
