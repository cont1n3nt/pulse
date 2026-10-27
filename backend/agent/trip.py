"""Состояние поездки: изменение через операции, проверка состыковок, сводка для модели.

Модуль не зависит от LangChain. Инструменты описывают изменение операцией (dict с ключом "op"),
reducer графа применяет операции по очереди. Поэтому параллельные вызовы инструментов
не затирают друг друга, а производные поля (этап, итог, проблемы) всегда пересчитаны.

Операции:
    {"op": "requirements", "changes": {...}}   поля Requirements, None = сбросить
    {"op": "assumptions", "items": [...]}
    {"op": "hotels" | "flights", "items": [...]} новая выдача
    {"op": "cart_add", "item": {...}}           CartItem; заменяет отель или перелёт того же плеча
    {"op": "cart_remove", "option_id": "..."}
    {"op": "order", "order_id": "..."}          заказ оформлен, корзина очищается
"""

from datetime import date, datetime, timedelta

from .adapters import hotels as hotels_db
from .contracts import CartItem, FlightOption, HotelOption, Issue, Requirements, TripState

AIRPORT_BUFFER_MIN = 90  # приехать в аэропорт до вылета
EARLY_CHECKIN_WAIT_H = 2  # ожидание заселения, о котором стоит предупредить
LATE_CHECKOUT_WAIT_H = 3  # ожидание после выезда, о котором стоит сказать


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
    elif kind == "cart_add":
        item = CartItem.model_validate(op["item"])
        trip.cart = [c for c in trip.cart if not _same_slot(c, item)] + [item]
    elif kind == "cart_remove":
        trip.cart = [c for c in trip.cart if c.option_id != op["option_id"]]
    elif kind == "order":
        trip.orders.append(op["order_id"])
        trip.cart = []
    else:
        raise ValueError(f"Неизвестная операция {kind}")
    trip.version += 1
    return trip


def _direction(flight: FlightOption) -> str | None:
    if flight.to_airport in hotels_db.AIRPORTS:
        return "outbound"
    if flight.from_airport in hotels_db.AIRPORTS:
        return "return"
    return None


def _same_slot(a: CartItem, b: CartItem) -> bool:
    """В корзине один отель и по одному перелёту на каждое плечо."""
    if a.kind != b.kind:
        return False
    return a.kind != "flight" or a.leg == b.leg


def refresh(trip: TripState) -> TripState:
    passengers = max(trip.requirements.guests, 1)
    for item in trip.cart:
        if item.kind == "flight":
            price = item.snapshot.get("price_rub")
            item.price_rub = price * passengers if price is not None else None
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

def make_cart_item(trip: TripState, option_id: str) -> CartItem | str:
    """Позиция корзины из текущей выдачи или текст ошибки для модели."""
    for hotel in trip.hotels:
        if hotel.id == option_id:
            return CartItem(
                kind="hotel", option_id=hotel.id, price_rub=hotel.total_price_rub,
                title=f"{hotel.name}, {hotel.check_in:%d.%m}–{hotel.check_out:%d.%m}, {hotel.nights} н.",
                snapshot=hotel.model_dump(mode="json"),
            )
    for flight in trip.flights:
        if flight.id == option_id:
            leg = _direction(flight)
            if leg is None:
                return "Этот перелёт не в Сочи и не из Сочи, в поездку его добавить нельзя."
            return CartItem(
                kind="flight", option_id=flight.id, leg=leg, price_rub=flight.price_rub,
                title=(f"{flight.carrier_name} {flight.number}, {_local(flight.depart_at):%d.%m %H:%M} "
                       f"{flight.from_airport}→{flight.to_airport}"),
                snapshot=flight.model_dump(mode="json"),
            )
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

    if hotel is None:
        add("no_hotel", "blocker", "В корзине нет отеля.")
    else:
        _check_hotel(hotel, r, add)

    if hotel and outbound:
        _check_arrival(hotel, outbound, add)
    if hotel and back:
        _check_departure(hotel, back, add)
    if outbound and back and back.depart_at <= outbound.arrive_at:
        add("flights_order", "blocker", "Обратный рейс вылетает раньше, чем прилетает рейс туда.")
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
    fresh = hotels_db.quote(hotel.id, hotel.check_in, hotel.check_out)
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
        name = hotels_db.AMENITY_NAMES.get(code, code)
        value = hotel.amenities.get(code)
        if value is False:
            add(f"amenity_{code}", "blocker", f"В {hotel.name} нет условия «{name}».")
        elif value is None:
            add(f"amenity_{code}", "warning", f"Условие «{name}» в {hotel.name} не подтверждено: стоит уточнить у отеля.")

    if r.guests > 2:
        add("capacity_unknown", "info",
            f"Гостей {r.guests}: вместимость номера в демо-данных не указана, тип номера уточняется при бронировании.")


def _check_arrival(hotel: HotelOption, flight: FlightOption, add) -> None:
    arrive = _local(flight.arrive_at)
    if arrive.date() > hotel.check_in:
        add("late_arrival", "blocker",
            f"Прилёт {arrive:%d.%m в %H:%M} — позже даты заезда {hotel.check_in:%d.%m}: "
            f"оплаченная ночь пропадёт.")
        return
    if arrive.date() < hotel.check_in:
        add("early_arrival", "blocker",
            f"Прилёт {arrive:%d.%m}, а заезд только {hotel.check_in:%d.%m}: на ночь до заезда нет жилья.")
        return
    transfer = hotels_db.transfer_minutes(hotel)
    at_hotel = arrive + timedelta(minutes=transfer or 0)
    check_in = datetime.combine(hotel.check_in, hotels_db.CHECK_IN_TIME)
    wait = check_in - at_hotel
    if wait >= timedelta(hours=EARLY_CHECKIN_WAIT_H):
        road = f", дорога из аэропорта около {transfer} мин" if transfer else ""
        add("early_checkin", "warning",
            f"Прилёт в {arrive:%H:%M}{road}, в отеле около {at_hotel:%H:%M}, а заселение с "
            f"{hotels_db.CHECK_IN_TIME:%H:%M}: ждать около {_hours(wait)}. "
            f"Можно запросить ранний заезд или оставить вещи на ресепшене.")


def _check_departure(hotel: HotelOption, flight: FlightOption, add) -> None:
    depart = _local(flight.depart_at)
    if depart.date() > hotel.check_out:
        add("late_departure", "blocker",
            f"Обратный вылет {depart:%d.%m}, а выезд из отеля {hotel.check_out:%d.%m}: на ночь перед вылетом нет жилья.")
        return
    if depart.date() < hotel.check_out:
        add("unused_night", "warning",
            f"Вылет {depart:%d.%m}, а отель оплачен до {hotel.check_out:%d.%m}: последняя ночь не используется.")
        return
    transfer = hotels_db.transfer_minutes(hotel) or 0
    leave = depart - timedelta(minutes=transfer + AIRPORT_BUFFER_MIN)
    check_out = datetime.combine(hotel.check_out, hotels_db.CHECK_OUT_TIME)
    if leave - check_out >= timedelta(hours=LATE_CHECKOUT_WAIT_H):
        add("late_checkout", "info",
            f"Выезд из отеля в {hotels_db.CHECK_OUT_TIME:%H:%M}, а ехать в аэропорт около {leave:%H:%M}: "
            f"можно запросить поздний выезд или оставить багаж.")


def _flight(trip: TripState, leg: str) -> FlightOption | None:
    item = next((c for c in trip.cart if c.kind == "flight" and c.leg == leg), None)
    return FlightOption.model_validate(item.snapshot) if item else None


# --- тексты для модели ----------------------------------------------------------------------

REQUIREMENT_LABELS = {
    "check_in": "заезд", "check_out": "выезд", "adults": "взрослых", "children_ages": "дети",
    "budget_rub": "бюджет на поездку", "district": "район", "must_have": "обязательные удобства",
    "near_sea": "близко к морю", "origin_code": "город вылета", "notes": "пожелания",
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
        f"Близко к морю: {'да' if r.near_sea else 'не важно'}. Город вылета: {r.origin_code or 'не указан'}.",
    ]
    if r.notes:
        lines.append(f"Пожелания: {'; '.join(r.notes)}.")
    if trip.assumptions:
        lines.append(f"Допущения: {'; '.join(trip.assumptions)}.")
    lines.append(f"Последняя выдача: отелей {len(trip.hotels)}, перелётов {len(trip.flights)}.")
    lines.append(describe_cart(trip))
    if trip.cart:
        lines.append(describe_issues(trip.issues))
    if trip.orders:
        lines.append(f"Оформленные заказы: {', '.join(trip.orders)}.")
    return "\n".join(lines)


# --- форматирование -------------------------------------------------------------------------

def _local(moment: datetime) -> datetime:
    """Время в часовом поясе курорта, без tzinfo: так его сравниваем с часами заселения."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(hotels_db.LOCAL_TZ).replace(tzinfo=None)


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
