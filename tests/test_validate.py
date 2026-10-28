"""Проверка поездки до оплаты: каждая несостыковка ловится кодом, а не моделью."""

from datetime import datetime, timedelta

from backend.agent import trip as trip_mod
from backend.agent.adapters import hotels as hotels_db
from backend.agent.contracts import Requirements, TripState
from backend.agent.trip import flight_item, hotel_item, refresh, transfer_item

from .conftest import CHECK_IN, CHECK_OUT, evening_back, flight, morning_out


def hotel(kind: str | None = None, **search):
    found = hotels_db.search(CHECK_IN, CHECK_OUT, limit=5000, diversify=False, **search)
    return next(h for h in found if kind is None or h.kind == kind)


def assembled(req: Requirements | None = None, outbound=None, back=None, chosen=None) -> TripState:
    req = req or Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2)
    chosen = chosen or hotel()
    cart = [hotel_item(chosen)]
    if outbound:
        cart.append(flight_item(outbound, "outbound"))
    if back:
        cart.append(flight_item(back, "return"))
    if (transfer := hotels_db.transfer(chosen, max(req.guests, 1))):
        cart.append(transfer_item(transfer))
    return refresh(TripState(requirements=req, cart=cart))


def codes(trip: TripState, severity: str | None = None) -> set[str]:
    return {i.code for i in trip.issues if severity is None or i.severity == severity}


def test_clean_trip_has_no_blockers():
    assert not codes(assembled(outbound=morning_out(), back=evening_back()), "blocker")


def test_arrival_after_check_in_day_is_blocker():
    late = flight(datetime.combine(CHECK_IN, datetime.min.time()).replace(hour=22), hours=4)  # прилёт после полуночи
    assert "late_arrival" in codes(assembled(outbound=late, back=evening_back()), "blocker")


def test_arrival_day_before_check_in_is_blocker():
    early = morning_out(CHECK_IN - timedelta(days=1))
    assert "early_arrival" in codes(assembled(outbound=early, back=evening_back()), "blocker")


def test_departure_after_check_out_is_blocker():
    late = evening_back(CHECK_OUT + timedelta(days=1))
    assert "late_departure" in codes(assembled(outbound=morning_out(), back=late), "blocker")


def test_early_morning_arrival_warns_about_check_in():
    dawn = flight(datetime.combine(CHECK_IN, datetime.min.time()).replace(hour=3), hours=2)
    trip = assembled(outbound=dawn, back=evening_back())
    assert "early_checkin" in codes(trip, "warning")
    assert not codes(trip, "blocker")


def test_hostel_with_children_is_blocker():
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2, children_ages=[5])
    assert "hostel_with_kids" in codes(assembled(req, chosen=hotel("hostel")), "blocker")


def test_over_budget_is_warning():
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2, budget_rub=1_000)
    trip = assembled(req, outbound=morning_out(), back=evening_back())
    assert "over_budget" in codes(trip, "warning")


def test_changed_dates_block_old_hotel():
    trip = assembled(outbound=morning_out(), back=evening_back())
    moved = trip_mod.apply(trip, {"op": "requirements", "changes": {
        "check_in": (CHECK_IN + timedelta(days=2)).isoformat(), "check_out": (CHECK_OUT + timedelta(days=2)).isoformat()}})
    assert "dates_mismatch" in codes(refresh(moved), "blocker")


def test_missing_hotel_is_blocker():
    trip = refresh(TripState(requirements=Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2),
                             cart=[flight_item(morning_out(), "outbound")]))
    assert "no_hotel" in codes(trip, "blocker")


def test_flight_prices_multiply_by_passengers():
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=3)
    trip = assembled(req, outbound=morning_out(price=5_000), back=evening_back(price=5_000))
    flights = [c for c in trip.cart if c.kind == "flight"]
    assert all(c.price_rub == 15_000 for c in flights)
    assert trip.cart_total_rub == sum(c.price_rub for c in trip.cart)


def test_new_hotel_moves_transfer():
    trip = assembled(outbound=morning_out(), back=evening_back())
    other = hotel(district="Лазаревское")
    after = refresh(trip_mod.apply(trip, {"op": "cart_add", "item": hotel_item(other).model_dump(mode="json")}))
    transfer = next(c for c in after.cart if c.kind == "transfer")
    assert transfer.snapshot["hotel_id"] == other.id
    assert [c.kind for c in after.cart].count("hotel") == 1


def test_itinerary_puts_issue_on_its_step():
    dawn = flight(datetime.combine(CHECK_IN, datetime.min.time()).replace(hour=3), hours=2)
    steps = trip_mod.itinerary(assembled(outbound=dawn, back=evening_back()))
    check_in = next(s for s in steps if s["kind"] == "checkin")
    assert any(i["code"] == "early_checkin" for i in check_in["issues"])
    assert [s["at"] for s in steps] == sorted(s["at"] for s in steps)
