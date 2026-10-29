"""После покупки: перенос заказа на другие даты и трансфер к купленному отелю."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.agent import aftersale
from backend.agent import trip as trip_mod
from backend.agent.adapters import hotels as hotels_db
from backend.agent.contracts import Order, Requirements, TripState
from backend.agent.trip import flight_item, hotel_item, refresh, transfer_item

from .conftest import CHECK_IN, CHECK_OUT, evening_back, flight, morning_out

SESSION = "chat-1"


def order(with_flights=True, with_transfer=True) -> Order:
    chosen = hotels_db.search(CHECK_IN, CHECK_OUT, limit=1)[0]
    items = [hotel_item(chosen)]
    if with_flights:
        items += [flight_item(morning_out(), "outbound"), flight_item(evening_back(), "return")]
    if with_transfer:
        items.append(transfer_item(hotels_db.transfer(chosen, 2)))
    trip = refresh(TripState(requirements=Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2), cart=items))
    return Order(id="PLS-TEST01", session_id=SESSION, status="confirmed", items=trip.cart,
                 total_rub=trip.cart_total_rub, created_at=datetime.now(timezone.utc))


def find_flight(origin, destination, day, arrive_by):
    """Рейс на любую дату: утром туда, вечером обратно."""
    start = datetime.combine(day, datetime.min.time())
    return flight(start.replace(hour=8 if arrive_by else 18), outbound=arrive_by is not None, origin="MOW")


def test_move_rebuilds_same_trip_on_new_dates():
    old = order()
    new_in, new_out = CHECK_IN + timedelta(days=2), CHECK_OUT + timedelta(days=2)
    items, notes = aftersale.moved(old, new_in, new_out, 2, "MOW", find_flight)
    assert [i.kind for i in items] == ["hotel", "flight", "flight", "transfer"]
    hotel = items[0].snapshot
    assert hotel["id"] == old.items[0].snapshot["id"]
    assert (hotel["check_in"], hotel["check_out"]) == (new_in.isoformat(), new_out.isoformat())
    assert not notes
    req = Requirements(check_in=new_in, check_out=new_out, adults=2)
    trip = refresh(TripState(requirements=req, cart=items, order_change={"order_id": old.id, "mode": "replace"}))
    assert not [i for i in trip.issues if i.severity == "blocker"], trip.issues
    text = aftersale.describe_move(old, trip.cart, trip.cart_total_rub)
    assert old.id in text and "Итого" in text


def test_move_to_same_dates_is_refused():
    with pytest.raises(aftersale.CannotChange):
        aftersale.moved(order(), CHECK_IN, CHECK_OUT, 2, "MOW", find_flight)


def test_move_without_tickets_keeps_hotel_and_says_so():
    items, notes = aftersale.moved(order(), CHECK_IN + timedelta(days=1), CHECK_OUT + timedelta(days=1), 2, "MOW",
                                   lambda *args: None)
    assert [i.kind for i in items] == ["hotel", "transfer"]
    assert len(notes) == 2


def test_hotel_only_order_moves_without_flights():
    items, _ = aftersale.moved(order(with_flights=False, with_transfer=False), CHECK_IN + timedelta(days=1),
                               CHECK_OUT + timedelta(days=1), 2, None, find_flight)
    assert [i.kind for i in items] == ["hotel"]


def test_order_from_other_chat_is_refused():
    with pytest.raises(aftersale.CannotChange):
        aftersale.check_active(order(), "PLS-TEST01", "someone-else")


def test_cancelled_order_cannot_change():
    with pytest.raises(aftersale.CannotChange):
        aftersale.check_active(order().model_copy(update={"status": "cancelled"}), "PLS-TEST01", SESSION)


def test_transfer_added_to_hotel_only_order():
    old = order(with_flights=False, with_transfer=False)
    item = aftersale.transfer_for(old, 2)
    assert item.kind == "transfer" and item.snapshot["hotel_id"] == old.items[0].snapshot["id"]
    # дополнение без отеля в корзине: отель уже куплен, блокера «нет отеля» нет
    trip = refresh(TripState(requirements=Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2),
                             cart=[item], order_change={"order_id": old.id, "mode": "extend"}))
    assert "no_hotel" not in {i.code for i in trip.issues}


def test_transfer_not_duplicated():
    with pytest.raises(aftersale.CannotChange):
        aftersale.transfer_for(order(), 2)


def test_order_op_records_replaced_order():
    trip = TripState(orders=["PLS-OLD"], order_change={"order_id": "PLS-OLD", "mode": "replace"})
    after = trip_mod.apply(trip, {"op": "order", "order_id": "PLS-NEW", "cancelled": "PLS-OLD"})
    assert after.orders == ["PLS-OLD", "PLS-NEW"] and after.cancelled_orders == ["PLS-OLD"]
    assert after.order_change is None and not after.cart
    assert "Отменённые" in trip_mod.summary(refresh(after))
