"""Инструменты после покупки: перенос оформленного заказа и трансфер к купленному отелю.

Оба собирают корзину (aftersale.py), а оплату подтверждает тот же checkout: перенос отменяет
старый заказ только после оплаты нового.
"""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from .. import aftersale, providers
from ..contracts import Requirements, SourceUnavailable, TripState
from ..trip import describe_cart, describe_issues, refresh
from ._state import BadInput, current_trip, emit_cart, prepare_changes, result, session_id


def _order(runtime: ToolRuntime, trip: TripState, order_id: str | None):
    active = [o for o in trip.orders if o not in trip.cancelled_orders]
    order_id = (order_id or (active[-1] if active else "")).strip().upper()
    if not order_id:
        raise aftersale.CannotChange("В этом чате ещё нет оформленных заказов.")
    return aftersale.check_active(providers.orders().get(order_id), order_id, session_id(runtime))


def _find_flight(origin, destination, day, arrive_by):
    try:
        return providers.flights().cheapest_on(origin, destination, day, arrive_by=arrive_by)
    except SourceUnavailable:
        return None


@tool
def change_order_dates(runtime: ToolRuntime, check_in: str | None = None, check_out: str | None = None,
                       order_id: str | None = None):
    """Перенести оформленный заказ на другие даты: тот же отель, та же дорога (самолёт или поезд)
    и трансфер на новые даты кладутся в корзину с разницей в рублях. Старый заказ отменится
    только после оплаты нового через checkout.

    Вызывай, когда заказ уже оформлен и пользователь просит его перенести («сдвиньте на день»,
    «можно на неделю позже»). Для нового подбора до покупки — plan_trip.

    Args:
        check_in: новая дата заезда YYYY-MM-DD. Если передать только её, длительность сохранится.
        check_out: новая дата выезда YYYY-MM-DD.
        order_id: номер заказа; по умолчанию последний оформленный в этом чате.
    """
    trip = current_trip(runtime)
    try:
        order = _order(runtime, trip, order_id)
        hotel = aftersale.ordered_hotel(order)
        # даты считаются от дат заказа, а не от текущих требований: их могли поменять после покупки
        base = trip.requirements.model_copy(update={"check_in": hotel.check_in, "check_out": hotel.check_out})
        changes, remark = prepare_changes(base, check_in=check_in, check_out=check_out)
        req = Requirements.model_validate(base.model_dump() | changes)
        guests = max(req.guests, 1)
        items, notes = aftersale.moved(order, req.check_in, req.check_out, guests, req.origin_code, _find_flight)
    except (aftersale.CannotChange, BadInput) as exc:
        return result(runtime, "change_order_dates", str(exc), [])

    change = {"order_id": order.id, "mode": "replace"}
    ops = [{"op": "requirements", "changes": {"check_in": req.check_in.isoformat(),
                                              "check_out": req.check_out.isoformat()}},
           {"op": "cart_set", "items": [i.model_dump(mode="json") for i in items], "order_change": change}]
    after = refresh(trip.model_copy(update={"requirements": req, "cart": items, "order_change": change}))
    emit_cart(runtime, after)
    lines = [remark.strip()] if remark else []
    lines += [aftersale.describe_move(order, after.cart, after.cart_total_rub), describe_issues(after.issues), *notes,
              "Назови разницу в рублях и спроси, оформлять ли перенос. Старый заказ отменится после оплаты нового; "
              "оплата и возврат — демо."]
    return result(runtime, "change_order_dates", "\n".join(lines), ops)


@tool
def add_order_transfer(runtime: ToolRuntime, order_id: str | None = None):
    """Добавить к оформленному заказу трансфер до отеля и обратно (из аэропорта, а если в заказе
    поезд — от вокзала). Кладёт трансфер в корзину отдельным заказом-дополнением.

    Вызывай, когда заказ уже оформлен без трансфера и пользователь просит такси или встречу.

    Args:
        order_id: номер заказа; по умолчанию последний оформленный в этом чате.
    """
    trip = current_trip(runtime)
    try:
        order = _order(runtime, trip, order_id)
        item = aftersale.transfer_for(order, max(trip.requirements.guests, 1))
    except aftersale.CannotChange as exc:
        return result(runtime, "add_order_transfer", str(exc), [])
    change = {"order_id": order.id, "mode": "extend"}
    ops = [{"op": "cart_set", "items": [item.model_dump(mode="json")], "order_change": change}]
    after = refresh(trip.model_copy(update={"cart": [item], "order_change": change}))
    emit_cart(runtime, after)
    text = (f"Трансфер к заказу {order.id} в корзине.\n{describe_cart(after)}\n{describe_issues(after.issues)}\n"
            "Назови цену и время в пути и спроси, оформлять ли; цена такси — демо.")
    return result(runtime, "add_order_transfer", text, ops)


TOOLS = [change_order_dates, add_order_transfer]
