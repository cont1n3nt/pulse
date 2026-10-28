"""Контрактный тест: ядро работает с любым источником, который реализует протоколы providers.py.

Здесь вместо OpenStreetMap, Aviasales и SQLite — пять отелей и заказы в памяти, как если бы
подключили API партнёра. Пакеты, проверка поездки и перенос заказа при этом не меняются.
"""

from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest

from backend.agent import aftersale, packages, providers
from backend.agent.contracts import HotelOption, Order, Requirements, TransferOption, TripState
from backend.agent.location import HERE
from backend.agent.trip import refresh

from .conftest import CHECK_IN, CHECK_OUT, evening_back, flight, morning_out

NIGHTLY = {"p-1": 3000, "p-2": 4500, "p-3": 6000, "p-4": 8000, "p-5": 11000}


def _hotel(hotel_id: str, stars: int) -> HotelOption:
    return HotelOption(
        id=hotel_id, source="mock", is_demo=False, name=f"Partner {hotel_id}", kind="hotel", stars=stars,
        district=HERE.district_names[0], lat=43.6, lon=39.7, distance_to_sea_m=200 * stars,
        amenities={"wifi": True, "kids": True}, meal="breakfast", free_cancellation=True,
        check_in_time=time(12, 0), check_out_time=time(11, 0))


class PartnerHotels:
    def __init__(self):
        self.catalog = {h: _hotel(h, stars) for h, stars in zip(NIGHTLY, (2, 3, 3, 4, 5))}

    def quote(self, hotel_id, check_in, check_out):
        hotel = self.catalog.get(hotel_id)
        if hotel is None:
            return None
        nights = (check_out - check_in).days
        return hotel.model_copy(update={
            "check_in": check_in, "check_out": check_out, "nights": nights, "available": True,
            "price_per_night_rub": NIGHTLY[hotel_id], "total_price_rub": NIGHTLY[hotel_id] * nights})

    def search(self, check_in, check_out, district=None, max_price_per_night=None, must_have=(),
               near_sea=False, exclude_kinds=(), limit=5, diversify=True, target_per_night=None):
        return [self.quote(h, check_in, check_out) for h in self.catalog][:limit]

    def get(self, hotel_id):
        return self.catalog.get(hotel_id)


class PartnerTransfers:
    def transfer(self, hotel, guests):
        return TransferOption(hotel_id=hotel.id, minutes=40, km=25, price_rub=2400 if guests < 5 else 3600,
                              car="standard" if guests < 5 else "minivan", is_demo=False)

    def transfer_minutes(self, hotel):
        return 40

    def for_guests(self, transfer, guests):
        return transfer


class PartnerFlights:
    def cheapest_on(self, origin, destination, day, arrive_by=None):
        return None

    def prices_around(self, origin, destination, day, days):
        return {}

    def fetch_flights(self, origin, destination, depart, return_date=None, limit=5):
        return []


class PartnerBooking:
    def __init__(self):
        self.orders: dict[str, Order] = {}

    def create(self, session_id, version, items, total_rub):
        order = Order(id=f"PRT-{len(self.orders) + 1}", session_id=session_id, status="confirmed", items=items,
                      total_rub=total_rub, created_at=datetime.now(timezone.utc))
        self.orders[order.id] = order
        return order

    def get(self, order_id):
        return self.orders.get(order_id)

    def cancel(self, order_id):
        self.orders[order_id] = self.orders[order_id].model_copy(update={"status": "cancelled"})


def partner():
    return SimpleNamespace(hotels=PartnerHotels(), transfers=PartnerTransfers(), flights=PartnerFlights(),
                           orders=PartnerBooking())


@pytest.fixture
def partner_api():
    found = partner()
    with providers.use(**vars(found)):
        yield found


def test_demo_adapters_implement_contracts():
    providers.check(providers.demo())


def test_unknown_provider_fails_at_start():
    with pytest.raises(ValueError):
        providers.load("nonexistent")
    with pytest.raises(TypeError):
        with providers.use(hotels=object()):
            pass


def test_packages_and_checks_run_on_partner_api(partner_api):
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2)
    ranked = providers.hotels().search(req.check_in, req.check_out)
    outbound, back = morning_out(), evening_back()
    built = packages.build(req, ranked, outbound, back)

    assert built and {p.hotel.id for p in built} <= set(NIGHTLY)
    for pkg in built:
        assert pkg.total_rub == pkg.hotel.total_price_rub + pkg.flights_rub + pkg.transfer.price_rub
        assert not [i for i in pkg.issues if i.severity == "blocker"]

    dawn = flight(datetime.combine(CHECK_IN, time(4, 0)), hours=2)
    trip = refresh(TripState(requirements=req, cart=packages.items(built[0].model_copy(update={"outbound": dawn}))))
    early = next(i for i in trip.issues if i.code == "early_checkin")
    assert "12:00" in early.message  # часы заселения этого отеля, а не типовые для локации


def test_order_change_runs_on_partner_api(partner_api):
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2)
    hotel = providers.hotels().quote("p-2", CHECK_IN, CHECK_OUT)
    cart = refresh(TripState(requirements=req, cart=packages.items(packages._package(
        "optimum", hotel, req, None, None, None)))).cart
    order = providers.orders().create("chat-1", 1, cart, sum(c.price_rub for c in cart))

    moved_in, moved_out = CHECK_IN.replace(day=CHECK_IN.day + 1), CHECK_OUT.replace(day=CHECK_OUT.day + 1)
    items, _ = aftersale.moved(order, moved_in, moved_out, 2, None, lambda *a: None)
    assert [i.kind for i in items] == ["hotel", "transfer"]
    assert items[0].price_rub == NIGHTLY["p-2"] * (moved_out - moved_in).days
