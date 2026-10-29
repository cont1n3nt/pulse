"""После покупки: перенос заказа на другие даты и трансфер к уже купленному отелю.

Модуль не зависит от LangChain и не ходит в сеть сам: поиск билетов ему передают функцией.
Как и пакеты, новую корзину собирает код: тот же отель на новые даты, та же дорога (самолёт
или поезд) и трансфер, если он был. Старый заказ отменяется только после оплаты нового
(checkout), а до этого перенос — обычная корзина с проверкой состыковок.
"""

from collections.abc import Callable
from datetime import date

from . import providers
from .adapters import trains
from .contracts import CartItem, FlightOption, HotelOption, Order, TransferOption
from .location import HERE
from .trip import _dates, _rub, flight_item, hotel_item, transfer_item

# (откуда, куда, день вылета, прилететь не позже дня | None) -> самый дешёвый рейс или None
FindFlight = Callable[[str, str, date, date | None], FlightOption | None]


class CannotChange(ValueError):
    """Перенос или дополнение невозможны: текст уходит модели."""


def _item(order: Order, kind: str, leg: str | None = None) -> CartItem | None:
    return next((c for c in order.items if c.kind == kind and (leg is None or c.leg == leg)), None)


def ordered_hotel(order: Order) -> HotelOption:
    item = _item(order, "hotel")
    if item is None:
        raise CannotChange(f"В заказе {order.id} нет отеля.")
    return HotelOption.model_validate(item.snapshot)


def _by_train(order: Order) -> bool:
    leg = _item(order, "flight", "outbound") or _item(order, "flight", "return")
    return leg is not None and leg.snapshot.get("mode") == "train"


def check_active(order: Order | None, order_id: str, session_id: str) -> Order:
    if order is None or order.session_id != session_id:
        raise CannotChange(f"Заказа {order_id} в этом чате нет.")
    if order.status != "confirmed":
        raise CannotChange(f"Заказ {order_id} уже отменён.")
    return order


def moved(order: Order, check_in: date, check_out: date, guests: int, origin: str | None,
          find_flight: FindFlight) -> tuple[list[CartItem], list[str]]:
    """Тот же заказ на новые даты: (позиции новой корзины, замечания для модели)."""
    if check_out <= check_in:
        raise CannotChange("Дата выезда должна быть позже заезда.")
    hotel = ordered_hotel(order)
    if (check_in, check_out) == (hotel.check_in, hotel.check_out):
        raise CannotChange(f"Заказ {order.id} уже на эти даты.")
    fresh = providers.hotels().quote(hotel.id, check_in, check_out)
    if fresh is None or not fresh.available:
        raise CannotChange(f"{hotel.name} на {_dates(check_in, check_out)} недоступен: предложи другие даты "
                           f"или новый подбор.")
    items, notes = [hotel_item(fresh)], []
    had_legs = _item(order, "flight") is not None
    had_transfer = _item(order, "transfer") is not None
    transfer: TransferOption | None = None

    if had_legs and _by_train(order):
        rail = trains.for_hotel(origin, check_in, check_out, fresh, guests) if origin else None
        if rail is None:
            notes.append("Поездов на новые даты не нашлось: в корзине только отель, скажи об этом.")
        else:
            outbound, back, transfer = rail
            items += [flight_item(outbound, "outbound"), flight_item(back, "return")]
    elif had_legs:
        if not origin:
            raise CannotChange("Неизвестен город вылета: уточни его у пользователя.")
        outbound = find_flight(origin, HERE.airport.code, check_in, check_in)
        back = find_flight(HERE.airport.code, origin, check_out, None)
        for flight, leg, day in ((outbound, "outbound", check_in), (back, "return", check_out)):
            if flight is None:
                notes.append(f"На {day:%d.%m} билета {'туда' if leg == 'outbound' else 'обратно'} в кэше "
                             f"Aviasales нет: в корзине без него, скажи об этом.")
            else:
                items.append(flight_item(flight, leg))
    if had_transfer:
        transfer = transfer or providers.transfers().transfer(fresh, guests)
        if transfer:
            items.append(transfer_item(transfer))
    return items, notes


def transfer_for(order: Order, guests: int) -> CartItem:
    """Трансфер к купленному отелю: от вокзала, если в заказе поезд, иначе из аэропорта."""
    if _item(order, "transfer") is not None:
        raise CannotChange(f"Трансфер уже есть в заказе {order.id}.")
    hotel = ordered_hotel(order)
    if _by_train(order) and trains.STATIONS:
        station, km = trains.nearest_station(hotel)
        transfer = trains.station_transfer(hotel, station, km, guests)
    else:
        transfer = providers.transfers().transfer(hotel, guests)
    if transfer is None:
        raise CannotChange(f"Для {hotel.name} нет данных о дороге: трансфер не посчитать.")
    return transfer_item(transfer)


def describe_move(order: Order, cart: list[CartItem], total: int) -> str:
    """Что меняется при переносе: даты, каждая позиция и итог в рублях. cart — пересчитанная
    корзина (цены перелётов уже за всех пассажиров)."""
    old_hotel = ordered_hotel(order)
    new_hotel = HotelOption.model_validate(cart[0].snapshot)
    lines = [f"Перенос заказа {order.id}: {_dates(old_hotel.check_in, old_hotel.check_out)} → "
             f"{_dates(new_hotel.check_in, new_hotel.check_out)}."]
    before = {(c.kind, c.leg): c for c in order.items}
    for item in cart:
        old = before.get((item.kind, item.leg))
        was = f"было {_rub(old.price_rub)}" if old else "новое"
        lines.append(f"- {item.title}: {_rub(item.price_rub)} ({was})")
    diff = total - order.total_rub
    sign = f"доплата {_rub(diff)}" if diff > 0 else f"дешевле на {_rub(-diff)}" if diff < 0 else "без доплаты"
    lines.append(f"Итого {_rub(total)} вместо {_rub(order.total_rub)}: {sign}.")
    return "\n".join(lines)
