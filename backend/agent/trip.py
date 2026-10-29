"""Состояние поездки: изменение через операции, проверка состыковок, сводка для модели.

Модуль не зависит от LangChain. Инструменты описывают изменение операцией (dict с ключом "op"),
reducer графа применяет операции по очереди. Поэтому параллельные вызовы инструментов
не затирают друг друга, а производные поля (этап, итог, проблемы) всегда пересчитаны.

Операции:
    {"op": "requirements", "changes": {...}}   поля Requirements, None = сбросить
    {"op": "assumptions", "items": [...]}
    {"op": "hotels" | "flights", "items": [...]} новая выдача
    {"op": "packages", "items": [...]}          готовые пакеты: перелёты + трансфер + отель
    {"op": "weather", "data": {...} | None}     погода на даты поездки
    {"op": "cart_add", "item": {...}}           CartItem; заменяет отель или перелёт того же плеча
    {"op": "cart_remove", "option_id": "..."}
    {"op": "cart_set", "items": [...], "order_change": {...} | None}
                                                корзина целиком: перенос заказа или дополнение к нему
    {"op": "order", "order_id": "...", "cancelled": "..." | None}
                                                заказ оформлен, корзина очищается; cancelled — заменённый заказ
"""

from datetime import date, datetime, time, timedelta

from . import providers
from .adapters import trains
from .contracts import AMENITY_NAMES, CartItem, FlightOption, HotelOption, Issue, Package, Requirements, TransferOption, TripState
from .location import HERE

AIRPORT_BUFFER_MIN = 90  # приехать в аэропорт до вылета
EARLY_CHECKIN_WAIT_H = 2  # ожидание заселения, о котором стоит предупредить
LATE_CHECKOUT_WAIT_H = 3  # ожидание после выезда, о котором стоит сказать
LONG_TRANSFER_MIN = 90  # дорога из аэропорта, о которой стоит предупредить
NIGHT_END_H = 5  # прибытие до этого часа в день заезда — ночь без жилья


# --- reducer --------------------------------------------------------------------------------

def reduce_trip(current: dict | None, ops: list[dict] | dict) -> dict:
    """Reducer поля trip в состоянии графа: применяет операции и пересчитывает производное."""
    trip = TripState.model_validate(current or {})
    for op in ops if isinstance(ops, list) else [ops]:
        trip = apply(trip, op)
    return refresh(trip).model_dump(mode="json")


def apply(trip: TripState, op: dict) -> TripState:
    trip = trip.model_copy(deep=True)
    kind = op["op"]
    if kind == "requirements":
        merged = trip.requirements.model_dump() | op["changes"]
        trip.requirements = Requirements.model_validate(merged)
    elif kind == "assumptions":
        trip.assumptions = list(dict.fromkeys(trip.assumptions + op["items"]))
    elif kind == "hotels":
        trip.hotels = [HotelOption.model_validate(x) for x in op["items"]]
    elif kind == "flights":
        # выдача «обратно» не стирает выдачу «туда», и наоборот
        new = [FlightOption.model_validate(x) for x in op["items"]]
        directions = {_direction(f) for f in new}
        trip.flights = [f for f in trip.flights if _direction(f) not in directions] + new
    elif kind == "packages":
        trip.packages = [Package.model_validate(x) for x in op["items"]]
    elif kind == "weather":
        trip.weather = op["data"]
    elif kind == "cart_add":
        item = CartItem.model_validate(op["item"])
        trip.cart = [c for c in trip.cart if not _same_slot(c, item)] + [item]
        if item.kind == "hotel":
            _follow_hotel(trip, HotelOption.model_validate(item.snapshot))
    elif kind == "cart_remove":
        trip.cart = [c for c in trip.cart if c.option_id != op["option_id"]]
        if not trip.cart:
            trip.order_change = None
    elif kind == "cart_set":
        trip.cart = [CartItem.model_validate(x) for x in op["items"]]
        trip.order_change = op.get("order_change")
    elif kind == "order":
        trip.orders.append(op["order_id"])
        if op.get("cancelled"):
            trip.cancelled_orders.append(op["cancelled"])
        trip.cart = []
        trip.order_change = None
    else:
        raise ValueError(f"Неизвестная операция {kind}")
    trip.version += 1
    return trip


def _direction(flight: FlightOption) -> str | None:
    arrivals = trains.STATIONS if flight.mode == "train" else (HERE.airport.code,)
    if flight.to_airport in arrivals:
        return "outbound"
    if flight.from_airport in arrivals:
        return "return"
    return None


def _follow_hotel(trip: TripState, hotel: HotelOption) -> None:
    """Сменился отель: трансфер в корзине пересчитывается на новый адрес."""
    old = next((c for c in trip.cart if c.kind == "transfer"), None)
    if old is None or old.snapshot.get("hotel_id") == hotel.id:
        return
    trip.cart = [c for c in trip.cart if c.kind != "transfer"]
    guests = max(trip.requirements.guests, 1)
    if old.snapshot.get("start", "аэропорт") != "аэропорт":
        station, km = trains.nearest_station(hotel)
        fresh = trains.station_transfer(hotel, station, km, guests)
    else:
        fresh = providers.transfers().transfer(hotel, guests)
    if fresh:
        trip.cart.append(transfer_item(fresh))


def _same_slot(a: CartItem, b: CartItem) -> bool:
    """В корзине один отель и по одному перелёту на каждое плечо."""
    if a.kind != b.kind:
        return False
    return a.kind != "flight" or a.leg == b.leg


def refresh(trip: TripState) -> TripState:
    passengers = max(trip.requirements.guests, 1)
    for i, item in enumerate(trip.cart):
        if item.kind == "flight":
            price = item.snapshot.get("price_rub")
            item.price_rub = price * passengers if price is not None else None
        elif item.kind == "transfer":
            # гостей стало больше или меньше: машина и цена такси под новое число гостей
            fresh = providers.transfers().for_guests(TransferOption.model_validate(item.snapshot), passengers)
            if fresh.price_rub != item.price_rub or fresh.car != item.snapshot.get("car"):
                trip.cart[i] = transfer_item(fresh)
    trip.cart_total_rub = sum(c.price_rub or 0 for c in trip.cart)
    trip.issues = validate(trip) if trip.cart else []
    trip.stage = stage(trip)
    return trip


def stage(trip: TripState) -> str:
    """Этап считается из состояния. «checkout» ставит сервис, пока ждёт подтверждения оплаты."""
    r = trip.requirements
    if trip.cart:
        return "assemble"
    if trip.orders:
        return "done"
    if trip.hotels:
        return "compare"
    if r.check_in and r.check_out and r.adults:
        return "search"
    return "clarify"


# --- корзина --------------------------------------------------------------------------------

def hotel_item(hotel: HotelOption) -> CartItem:
    return CartItem(
        kind="hotel", option_id=hotel.id, price_rub=hotel.total_price_rub,
        title=f"{hotel.name}, {hotel.check_in:%d.%m}–{hotel.check_out:%d.%m}, {hotel.nights} н.",
        snapshot=hotel.model_dump(mode="json"),
    )


def flight_item(flight: FlightOption, leg: str) -> CartItem:
    return CartItem(
        kind="flight", option_id=flight.id, leg=leg, price_rub=flight.price_rub,
        title=(f"{flight.carrier_name} {flight.number}, {_local(flight.depart_at):%d.%m %H:%M} "
               f"{flight.from_airport}→{flight.to_airport}"),
        snapshot=flight.model_dump(mode="json"),
    )


def transfer_item(transfer: TransferOption) -> CartItem:
    car = "минивэн" if transfer.car == "minivan" else "такси"
    return CartItem(
        kind="transfer", option_id=f"transfer-{transfer.hotel_id}", price_rub=transfer.price_rub,
        title=f"Трансфер {transfer.start} ⇄ отель, {car}, {transfer.trips} поездки по ~{transfer.minutes} мин",
        snapshot=transfer.model_dump(mode="json"),
    )


def make_cart_item(trip: TripState, option_id: str) -> CartItem | str:
    """Позиция корзины из текущей выдачи или текст ошибки для модели."""
    for hotel in trip.hotels + [p.hotel for p in trip.packages]:
        if hotel.id == option_id:
            return hotel_item(hotel)
    for flight in trip.flights:
        if flight.id == option_id:
            leg = _direction(flight)
            if leg is None:
                return f"Этот перелёт не {HERE.names.within} и не оттуда, в поездку его добавить нельзя."
            return flight_item(flight, leg)
    return (f"Варианта {option_id} нет в последней выдаче. Добавлять можно только отели и перелёты "
            f"из последнего поиска; если условия менялись, сначала повтори поиск.")


# --- проверка поездки -----------------------------------------------------------------------

def validate(trip: TripState) -> list[Issue]:
    issues: list[Issue] = []
    r = trip.requirements

    def add(code: str, severity: str, message: str) -> None:
        issues.append(Issue(code=code, severity=severity, message=message))

    hotel_item = next((c for c in trip.cart if c.kind == "hotel"), None)
    hotel = HotelOption.model_validate(hotel_item.snapshot) if hotel_item else None
    outbound = _flight(trip, "outbound")
    back = _flight(trip, "return")

    extends = (trip.order_change or {}).get("mode") == "extend"
    if hotel is None:
        if not extends:  # дополнение к заказу: отель уже куплен в нём
            add("no_hotel", "blocker", "В корзине нет отеля.")
    else:
        _check_hotel(hotel, r, add)

    transfer = next((c for c in trip.cart if c.kind == "transfer"), None)
    road = transfer.snapshot.get("minutes") if transfer else None
    if hotel and outbound:
        _check_arrival(hotel, outbound, road, add)
    if hotel and back:
        _check_departure(hotel, back, road, add)
    if outbound and back and back.depart_at <= outbound.arrive_at:
        add("flights_order", "blocker", "Обратный рейс вылетает раньше, чем прилетает рейс туда.")
    if transfer and transfer.snapshot.get("minutes", 0) >= LONG_TRANSFER_MIN:
        start = transfer.snapshot.get("start", "аэропорт")
        origin = "аэропорта" if start == "аэропорт" else start.replace("вокзал", "вокзала")
        add("long_transfer", "warning" if r.children_ages else "info",
            f"Дорога от {origin} до отеля около {transfer.snapshot['minutes']} мин в одну сторону.")
    for item in trip.cart:
        if item.kind == "flight" and item.price_rub is None:
            add("flight_price_unknown", "info", f"Цена рейса {item.title} неизвестна и в итог не вошла.")

    if r.budget_rub and trip.cart_total_rub > r.budget_rub:
        add("over_budget", "warning",
            f"Итого {_rub(trip.cart_total_rub)} — на {_rub(trip.cart_total_rub - r.budget_rub)} "
            f"больше бюджета {_rub(r.budget_rub)}.")
    return issues


def _check_hotel(hotel: HotelOption, r: Requirements, add) -> None:
    if r.check_in and r.check_out and (hotel.check_in, hotel.check_out) != (r.check_in, r.check_out):
        add("dates_mismatch", "blocker",
            f"Отель в корзине на {_dates(hotel.check_in, hotel.check_out)}, а поездка сейчас "
            f"{_dates(r.check_in, r.check_out)}: нужно заново подобрать отель на новые даты.")
    fresh = providers.hotels().quote(hotel.id, hotel.check_in, hotel.check_out)
    if fresh is None or not fresh.available:
        add("unavailable", "blocker", f"{hotel.name} больше недоступен на эти даты.")
    elif fresh.total_price_rub != hotel.total_price_rub:
        add("price_changed", "warning",
            f"Цена {hotel.name} изменилась: было {_rub(hotel.total_price_rub)}, "
            f"стало {_rub(fresh.total_price_rub)}.")

    required = list(r.must_have)
    if r.children_ages:
        if hotel.kind == "hostel":
            add("hostel_with_kids", "blocker", "С детьми выбран хостел.")
        if "kids" not in required:
            required.append("kids")
    for code in required:
        name = AMENITY_NAMES.get(code, code)
        value = hotel.amenities.get(code)
        if value is False:
            add(f"amenity_{code}", "blocker", f"В {hotel.name} нет условия «{name}».")
        elif value is None:
            add(f"amenity_{code}", "warning", f"Условие «{name}» в {hotel.name} не подтверждено: стоит уточнить у отеля.")

    if r.guests > 2:
        add("capacity_unknown", "info",
            f"Гостей {r.guests}: вместимость номера в демо-данных не указана, тип номера уточняется при бронировании.")


def _check_arrival(hotel: HotelOption, flight: FlightOption, road: int | None, add) -> None:
    arrive = _local(flight.arrive_at)
    train = flight.mode == "train"
    word = "Прибытие поезда" if train else "Прилёт"
    if arrive.date() > hotel.check_in:
        add("late_arrival", "blocker",
            f"{word} {arrive:%d.%m в %H:%M} — позже даты заезда {hotel.check_in:%d.%m}: "
            f"оплаченная ночь пропадёт.")
        return
    if arrive.date() < hotel.check_in:
        add("early_arrival", "blocker",
            f"{word} {arrive:%d.%m}, а заезд только {hotel.check_in:%d.%m}: на ночь до заезда нет жилья.")
        return
    transfer = road if road is not None else providers.transfers().transfer_minutes(hotel)
    at_hotel = arrive + timedelta(minutes=transfer or 0)
    check_in = datetime.combine(hotel.check_in, check_in_time(hotel))
    wait = check_in - at_hotel
    if arrive.hour < NIGHT_END_H:
        # прилёт ночью в день заезда: номер оплачен только со следующей ночи
        add("night_arrival", "warning",
            f"{word} {arrive:%d.%m в %H:%M} — ночью, а заселение с {check_in_time(hotel):%H:%M}: "
            f"эта ночь без жилья, ждать около {_hours(wait)}. Лучше рейс днём или отель с ночи "
            f"{hotel.check_in - timedelta(days=1):%d.%m}.")
    elif wait >= timedelta(hours=EARLY_CHECKIN_WAIT_H):
        place = "с вокзала" if train else "из аэропорта"
        road_text = f", дорога {place} около {transfer} мин" if transfer else ""
        add("early_checkin", "warning",
            f"{word} в {arrive:%H:%M}{road_text}, в отеле около {at_hotel:%H:%M}, а заселение с "
            f"{check_in_time(hotel):%H:%M}: ждать около {_hours(wait)}. "
            f"Можно запросить ранний заезд или оставить вещи на ресепшене.")


def _check_departure(hotel: HotelOption, flight: FlightOption, road: int | None, add) -> None:
    depart = _local(flight.depart_at)
    train = flight.mode == "train"
    word = "Обратный поезд" if train else "Обратный вылет"
    if depart.date() > hotel.check_out:
        add("late_departure", "blocker",
            f"{word} {depart:%d.%m}, а выезд из отеля {hotel.check_out:%d.%m}: на ночь перед отъездом нет жилья.")
        return
    if depart.date() < hotel.check_out:
        add("unused_night", "warning",
            f"{word} {depart:%d.%m}, а отель оплачен до {hotel.check_out:%d.%m}: последняя ночь не используется.")
        return
    transfer = road if road is not None else (providers.transfers().transfer_minutes(hotel) or 0)
    buffer = trains.BOARDING_BUFFER_MIN if train else AIRPORT_BUFFER_MIN
    leave = depart - timedelta(minutes=transfer + buffer)
    check_out = datetime.combine(hotel.check_out, check_out_time(hotel))
    if leave - check_out >= timedelta(hours=LATE_CHECKOUT_WAIT_H):
        place = "на вокзал" if train else "в аэропорт"
        add("late_checkout", "info",
            f"Выезд из отеля в {check_out_time(hotel):%H:%M}, а ехать {place} около {leave:%H:%M}: "
            f"можно запросить поздний выезд или оставить багаж.")


def _flight(trip: TripState, leg: str) -> FlightOption | None:
    item = next((c for c in trip.cart if c.kind == "flight" and c.leg == leg), None)
    return FlightOption.model_validate(item.snapshot) if item else None


# --- маршрут поездки -------------------------------------------------------------------------

ARRIVAL_ISSUES = ("late_arrival", "early_arrival", "night_arrival", "early_checkin")
DEPARTURE_ISSUES = ("late_departure", "unused_night", "late_checkout", "flights_order")
HOTEL_ISSUES = ("unavailable", "price_changed", "dates_mismatch", "hostel_with_kids")


def itinerary(trip: TripState) -> list[dict]:
    """Таймлайн собранной поездки для фронта: вылет → трансфер → заселение → дни → выезд → обратно.

    Проблемы проверки привязаны к шагу, на котором они случатся.
    """
    hotel_item_ = next((c for c in trip.cart if c.kind == "hotel"), None)
    if hotel_item_ is None:
        return []
    hotel = HotelOption.model_validate(hotel_item_.snapshot)
    outbound, back = _flight(trip, "outbound"), _flight(trip, "return")
    transfer = next((c for c in trip.cart if c.kind == "transfer"), None)
    minutes = transfer.snapshot.get("minutes") if transfer else providers.transfers().transfer_minutes(hotel)
    codes = {i.code: i for i in trip.issues}
    weather = {d["date"]: d for d in (trip.weather or {}).get("days", [])}
    sea = ((trip.weather or {}).get("sea") or {}).get("t")

    def issues(group) -> list[dict]:
        return [codes[c].model_dump() for c in group if c in codes]

    steps = []
    check_in = datetime.combine(hotel.check_in, check_in_time(hotel))
    if outbound:
        dep, arr = _local(outbound.depart_at), _local(outbound.arrive_at)
        steps.append(_leg_step(outbound, dep, arr))
        if minutes:
            steps.append({"kind": "transfer", "at": arr.isoformat(), "title": "Трансфер в отель",
                          "detail": f"около {minutes} мин", "issues": []})
        # заселиться можно не раньше, чем доедешь: прилёт вечером — шаг после трансфера, а не в 14:00
        at_hotel = arr + timedelta(minutes=minutes or 0)
        if at_hotel.date() == hotel.check_in and at_hotel > check_in:
            check_in = at_hotel
    steps.append({"kind": "checkin", "at": check_in.isoformat(), "title": f"Заселение: {hotel.name}",
                  "detail": f"с {check_in_time(hotel):%H:%M}, {hotel.district}",
                  "issues": issues(ARRIVAL_ISSUES + HOTEL_ISSUES)})
    day = hotel.check_in
    while day <= hotel.check_out:
        w = weather.get(day.isoformat())
        if w and day not in (hotel.check_in,):
            detail = f"{w['t_max']}°/{w['t_min']}°, {w['summary']}"
            if sea is not None:
                detail += f", море {sea}°"
            steps.append({"kind": "day", "at": datetime.combine(day, datetime.min.time()).isoformat(),
                          "title": f"{day:%d.%m}", "detail": detail, "issues": []})
        day += timedelta(days=1)
    check_out = datetime.combine(hotel.check_out, check_out_time(hotel))
    steps.append({"kind": "checkout", "at": check_out.isoformat(), "title": "Выезд из отеля",
                  "detail": f"до {check_out_time(hotel):%H:%M}", "issues": issues(DEPARTURE_ISSUES)})
    if back:
        dep, arr = _local(back.depart_at), _local(back.arrive_at)
        steps.append(_leg_step(back, dep, arr))
    steps.sort(key=lambda s: s["at"])
    return steps


def _leg_step(leg: FlightOption, dep: datetime, arr: datetime) -> dict:
    if leg.mode == "train":
        return {"kind": "train", "at": dep.isoformat(), "title": f"Поезд {leg.from_airport} → {leg.to_airport}",
                "detail": f"{leg.number}, в пути {_hours(arr - dep)}, прибытие {arr:%d.%m в %H:%M} "
                          f"({trains.source_note(leg)})",
                "issues": []}
    return {"kind": "flight", "at": dep.isoformat(), "title": f"Вылет {leg.from_airport} → {leg.to_airport}",
            "detail": f"{leg.carrier_name} {leg.number}, прилёт в {arr:%H:%M}", "issues": []}


# --- тексты для модели ----------------------------------------------------------------------

REQUIREMENT_LABELS = {
    "check_in": "заезд", "check_out": "выезд", "adults": "взрослых", "children_ages": "дети",
    "budget_rub": "бюджет на поездку", "district": "район", "must_have": "обязательные удобства",
    "near_sea": "близко к морю", "origin_code": "город вылета", "transport": "транспорт", "notes": "пожелания",
}


def diff_requirements(old: Requirements, new: Requirements) -> list[str]:
    changes = []
    for field, label in REQUIREMENT_LABELS.items():
        before, after = getattr(old, field), getattr(new, field)
        if before != after:
            changes.append(f"{label}: {_value(before)} → {_value(after)}")
    return changes


def diff_hotels(old: list[HotelOption], new: list[HotelOption]) -> list[str]:
    """Чем новая выдача отличается от прошлой: для ответа «что изменилось»."""
    if not old:
        return []
    before = {h.id: h for h in old}
    after = {h.id: h for h in new}
    lines = []
    for hotel in new:
        prev = before.get(hotel.id)
        if prev is None:
            lines.append(f"новый вариант: {hotel.name}")
        elif prev.total_price_rub != hotel.total_price_rub:
            lines.append(f"{hotel.name}: итог {_rub(prev.total_price_rub)} → {_rub(hotel.total_price_rub)}")
        else:
            lines.append(f"{hotel.name}: без изменений")
    lines += [f"выпал из выдачи: {h.name}" for h in old if h.id not in after]
    return lines


def describe_cart(trip: TripState) -> str:
    if not trip.cart:
        return "Корзина пуста."
    lines = [f"- {c.title}: {_rub(c.price_rub) if c.price_rub is not None else 'цена неизвестна'}"
             for c in trip.cart]
    lines.append(f"Итого: {_rub(trip.cart_total_rub)} (ориентировочно, демо).")
    return "Корзина:\n" + "\n".join(lines)


def describe_issues(issues: list[Issue]) -> str:
    if not issues:
        return "Проблем в сборке поездки нет."
    labels = {"blocker": "мешает оформить", "warning": "предупреждение", "info": "к сведению"}
    return "Проверка поездки:\n" + "\n".join(f"- [{labels[i.severity]}] {i.message}" for i in issues)


def summary(trip: TripState) -> str:
    """Сводка состояния для системного промпта: модель видит её на каждом шаге."""
    r = trip.requirements
    guests = "не указано"
    if r.adults:
        guests = f"{r.adults} взр."
        if r.children_ages:
            guests += f", дети: {', '.join(map(str, r.children_ages))} лет"
    dates = _dates(r.check_in, r.check_out) if r.check_in and r.check_out else "не указаны"
    lines = [
        f"Этап: {trip.stage}, версия {trip.version}.",
        f"Даты: {dates}. Гости: {guests}. Бюджет: {_rub(r.budget_rub) if r.budget_rub else 'не указан'}.",
        f"Район: {r.district or 'любой'}. Удобства: {', '.join(r.must_have) or 'нет'}. "
        f"Близко к морю: {'да' if r.near_sea else 'не важно'}. Город вылета: {r.origin_code or 'не указан'}. "
        f"Транспорт: {'поезд' if r.transport == 'train' else 'самолёт'}.",
    ]
    if r.notes:
        lines.append(f"Пожелания: {'; '.join(r.notes)}.")
    if trip.assumptions:
        lines.append(f"Допущения: {'; '.join(trip.assumptions)}.")
    lines.append(f"Последняя выдача: отелей {len(trip.hotels)}, перелётов {len(trip.flights)}, "
                 f"пакетов {len(trip.packages)}.")
    if trip.packages:
        lines.append("Пакеты: " + "; ".join(f"[{p.id}] {p.label} — {p.hotel.name}, итого {_rub(p.total_rub)}"
                                            for p in trip.packages) + ".")
    lines.append(describe_cart(trip))
    if trip.order_change:
        change = trip.order_change
        what = ("перенос заказа: после оплаты он заменит" if change["mode"] == "replace"
                else "дополнение к заказу")
        lines.append(f"Корзина — {what} {change['order_id']}.")
    if trip.cart:
        lines.append(describe_issues(trip.issues))
    active = [o for o in trip.orders if o not in trip.cancelled_orders]
    if active:
        lines.append(f"Оформленные заказы: {', '.join(active)}.")
    if trip.cancelled_orders:
        lines.append(f"Отменённые (заменённые) заказы: {', '.join(trip.cancelled_orders)}.")
    return "\n".join(lines)


# --- форматирование -------------------------------------------------------------------------

def _local(moment: datetime) -> datetime:
    """Время в часовом поясе курорта, без tzinfo: так его сравниваем с часами заселения."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(HERE.tz).replace(tzinfo=None)


def check_in_time(hotel: HotelOption) -> time:
    """Заселение: часы объекта, а если источник их не дал — типовые для локации."""
    return hotel.check_in_time or HERE.check_in


def check_out_time(hotel: HotelOption) -> time:
    return hotel.check_out_time or HERE.check_out


def _rub(value: int | None) -> str:
    return f"{value:,} ₽".replace(",", " ") if value is not None else "—"


def _dates(start: date, end: date) -> str:
    return f"{start:%d.%m.%Y}–{end:%d.%m.%Y} ({(end - start).days} н.)"


def _hours(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


def _value(value) -> str:
    if value is None or value == [] or value is False:
        return "—"
    if value is True:
        return "да"
    if isinstance(value, date):
        return f"{value:%d.%m.%Y}"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)
