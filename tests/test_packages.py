"""Пакеты собирает код: суммы сходятся, уровни упорядочены, ограничения соблюдены."""

from datetime import date, timedelta

from backend.agent import packages
from backend.agent.adapters import hotels as hotels_db
from backend.agent.contracts import Requirements

from .conftest import CHECK_IN, CHECK_OUT, evening_back, morning_out


def candidates(req: Requirements):
    return hotels_db.search(req.check_in, req.check_out, req.district, None, (), req.near_sea,
                            exclude_kinds=("hostel",) if req.children_ages else (), limit=600, diversify=False)


def build(**kw):
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2, **kw)
    return req, packages.build(req, candidates(req), morning_out(), evening_back())


def test_three_tiers_ordered_by_price():
    _, built = build(budget_rub=150_000)
    assert [p.id for p in built] == ["economy", "optimum", "comfort"]
    economy, optimum, comfort = built
    assert economy.total_rub < optimum.total_rub < comfort.total_rub


def test_total_is_sum_of_parts():
    req, built = build(budget_rub=150_000)
    for p in built:
        flights = (p.outbound.price_rub + p.back.price_rub) * req.guests
        assert p.flights_rub == flights
        assert p.total_rub == p.hotel.total_price_rub + flights + p.transfer.price_rub
        assert p.per_person_rub == round(p.total_rub / req.guests)


def test_budget_respected():
    req, built = build(budget_rub=120_000)
    by_id = {p.id: p for p in built}
    assert by_id["optimum"].within_budget and by_id["optimum"].total_rub <= req.budget_rub
    assert by_id["economy"].total_rub <= req.budget_rub
    # Комфорт может предложить доплату, но не больше чем на 15% сверх бюджета
    if "comfort" in by_id:
        assert by_id["comfort"].total_rub <= req.budget_rub * packages.COMFORT_STRETCH


def test_tradeoff_states_difference_in_rubles():
    _, built = build(budget_rub=150_000)
    optimum = next(p for p in built if p.id == "optimum")
    for p in built:
        if p.id == "optimum":
            continue
        diff = abs(p.total_rub - optimum.total_rub)
        assert packages._rub(diff) in p.tradeoffs[0]


def test_no_hostels_with_children():
    _, built = build(children_ages=[6], budget_rub=150_000)
    assert built and all(p.hotel.kind != "hostel" for p in built)


def test_district_filter():
    _, built = build(district="Красная Поляна", budget_rub=250_000)
    assert built and all(p.hotel.district == "Красная Поляна" for p in built)


def test_packages_pass_their_own_check():
    """Рейсы утром туда и вечером обратно: в пакетах нет блокеров по состыковкам."""
    _, built = build(budget_rub=150_000)
    for p in built:
        assert not [i for i in p.issues if i.severity == "blocker"], p.issues


def test_train_packages_arrive_on_check_in_day():
    req = Requirements(check_in=CHECK_IN, check_out=CHECK_OUT, adults=2, budget_rub=150_000, transport="train")
    built = packages.build(req, candidates(req), None, None, rail_from="MOW")
    assert built
    for p in built:
        assert p.outbound.mode == "train" and p.back.mode == "train"
        assert p.transfer.start.startswith("вокзал")
        assert not [i for i in p.issues if i.code in ("late_arrival", "early_arrival")]


def test_flex_dates_finds_cheaper_shift():
    req, built = build(budget_rub=150_000)
    hotel = next(p for p in built if p.id == "optimum").hotel
    days = range(-packages.FLEX_DAYS, packages.FLEX_DAYS + 1)
    out_prices = {CHECK_IN + timedelta(days=d): morning_out(CHECK_IN + timedelta(days=d), price=12_000) for d in days}
    back_prices = {CHECK_OUT + timedelta(days=d): evening_back(CHECK_OUT + timedelta(days=d), price=12_000) for d in days}
    # на день позже перелёты на 9 000 ₽ дешевле каждый
    out_prices[CHECK_IN + timedelta(days=1)] = morning_out(CHECK_IN + timedelta(days=1), price=3_000)
    back_prices[CHECK_OUT + timedelta(days=1)] = evening_back(CHECK_OUT + timedelta(days=1), price=3_000)
    flex = packages.flex_dates(req, hotel, out_prices, back_prices)
    assert flex is not None and flex["shift_days"] == 1
    assert date.fromisoformat(flex["check_in"]) == CHECK_IN + timedelta(days=1)
    assert flex["saving_rub"] >= packages.FLEX_MIN_SAVING_RUB


def test_flex_dates_silent_without_real_saving():
    req, built = build(budget_rub=150_000)
    hotel = next(p for p in built if p.id == "optimum").hotel
    days = range(-packages.FLEX_DAYS, packages.FLEX_DAYS + 1)
    same_out = {CHECK_IN + timedelta(days=d): morning_out(CHECK_IN + timedelta(days=d)) for d in days}
    same_back = {CHECK_OUT + timedelta(days=d): evening_back(CHECK_OUT + timedelta(days=d)) for d in days}
    flex = packages.flex_dates(req, hotel, same_out, same_back)
    assert flex is None or flex["saving_rub"] >= packages.FLEX_MIN_SAVING_RUB


def test_prices_are_deterministic():
    first = [(h.id, h.total_price_rub) for h in hotels_db.search(CHECK_IN, CHECK_OUT, limit=20)]
    second = [(h.id, h.total_price_rub) for h in hotels_db.search(CHECK_IN, CHECK_OUT, limit=20)]
    assert first == second
