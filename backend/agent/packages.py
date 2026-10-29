"""Пакеты поездки: перелёт (или поезд) туда и обратно + трансфер + отель, с одной итоговой суммой.

Модуль не зависит от LangChain и не ходит в сеть сам: перелёты и календарь цен ему передают.
Пакеты собирает код, а не модель: модель только объясняет выбор, поэтому суммы всегда сходятся.

Уровни: «Оптимум» — лучший по ранжированию отель, который укладывается в бюджет; «Эконом» —
заметно дешевле Оптимума; «Комфорт» — выше классом или ближе к морю за доплату. Если уровни
совпадают (мало вариантов), пакетов меньше трёх.
"""

from datetime import date, timedelta

from . import providers
from .adapters import trains
from .contracts import CartItem, FlightOption, HotelOption, Package, Requirements, TripState
from .location import HERE
from .trip import _local, _rub, flight_item, hotel_item, refresh, transfer_item

LABELS = {"economy": "Эконом", "optimum": "Оптимум", "comfort": "Комфорт"}
MIN_GAP = 0.07  # Эконом и Комфорт должны отличаться от Оптимума хотя бы на 7% цены
COMFORT_STRETCH = 1.15  # Комфорт может быть дороже бюджета не больше чем на 15%
ECONOMY_GAP = 0.2  # Эконом ищем сначала среди вариантов хотя бы на 20% дешевле Оптимума
TRANSFER_DIFF_MIN = 15  # разницу в дороге меньше этой не упоминаем
COMFORT_TRANSFER_MIN = 45  # дорога из аэропорта, которая не считается недостатком
OPTIMUM_WINDOW = 40  # Оптимум выбирается среди первых мест ранжирования
TRANSFER_WEIGHT = 0.4  # штраф за каждые 2 часа сверх нормы (доля шкалы ранжирования)
TRANSFER_WEIGHT_KIDS = 0.6
FLEX_DAYS = 3
FLEX_MIN_SAVING_RUB = 2000
FLEX_MIN_SAVING_SHARE = 0.05
KINDS = {"hotel": "отель", "guest_house": "гостевой дом", "apartment": "апартаменты",
         "hostel": "хостел", "resort": "санаторий/курорт"}
MEALS = {"none": "без питания", "breakfast": "с завтраком", "half_board": "полупансион",
         "all_inclusive": "всё включено"}


def flights_cost(outbound: FlightOption | None, back: FlightOption | None, passengers: int) -> int | None:
    prices = [f.price_rub for f in (outbound, back) if f is not None]
    if not prices or any(p is None for p in prices):
        return None
    return sum(prices) * passengers


def items(pkg: Package) -> list[CartItem]:
    out = [hotel_item(pkg.hotel)]
    for flight, leg in ((pkg.outbound, "outbound"), (pkg.back, "return")):
        if flight is not None:
            out.append(flight_item(flight, leg))
    if pkg.transfer is not None:
        out.append(transfer_item(pkg.transfer))
    return out


def _package(tier: str, hotel: HotelOption, req: Requirements, outbound, back, rail_from: str | None) -> Package:
    guests = max(req.guests, 1)
    by_rail = trains.for_hotel(rail_from, req.check_in, req.check_out, hotel, guests) if rail_from else None
    if by_rail:
        # поездом выходят на станции у отеля: своё время прибытия и такси от вокзала
        outbound, back, transfer = by_rail
    else:
        transfer = providers.transfers().transfer(hotel, guests)
    flights = flights_cost(outbound, back, guests)
    total = hotel.total_price_rub + (flights or 0) + (transfer.price_rub if transfer else 0)
    return Package(
        id=tier, label=LABELS[tier], hotel=hotel, outbound=outbound, back=back, transfer=transfer,
        flights_rub=flights, total_rub=total, per_person_rub=round(total / guests),
        within_budget=total <= req.budget_rub if req.budget_rub else None,
        highlights=highlights(hotel, transfer),
    )


def build(req: Requirements, ranked: list[HotelOption], outbound: FlightOption | None,
          back: FlightOption | None, rail_from: str | None = None) -> list[Package]:
    """Три пакета из отелей, отсортированных по ранжированию поиска.

    rail_from — код города: тогда вместо перелётов поезда до станции у каждого отеля.
    """
    if not ranked:
        return []
    all_options = [_package("optimum", h, req, outbound, back, rail_from) for h in ranked]
    pool = [p for p in all_options if p.within_budget is not False]
    if not pool:
        # в бюджет не влезает ничего: показываем самые дешёвые и честно помечаем
        pool = sorted(all_options, key=lambda p: p.total_rub)[:3]
    if any(p.hotel.kind != "hostel" for p in pool) and (req.children_ages or len(pool) > 3):
        pool = [p for p in pool if p.hotel.kind != "hostel"]

    # Оптимум: место в ранжировании отелей минус штраф за долгую дорогу из аэропорта —
    # в полной поездке полтора часа такси с ребёнком важнее звезды
    optimum = max(enumerate(pool), key=lambda ip: _fit(ip[0], len(pool), ip[1], req))[1]
    rest = [p for p in pool if p.hotel.id != optimum.hotel.id]
    # Эконом: лучший из заметно более дешёвых, а не самый дешёвый — иначе это всегда безымянная
    # комната на окраине. Дорогу учитываем и здесь: три часа такси с ребёнком — не экономия.
    cheaper = [p for p in rest if p.total_rub <= optimum.total_rub * (1 - ECONOMY_GAP)] \
        or [p for p in rest if p.total_rub <= optimum.total_rub * (1 - MIN_GAP)]
    economy = max(enumerate(cheaper), key=lambda ip: _fit(ip[0], len(cheaper), ip[1], req))[1] if cheaper else None
    # Комфорт может немного выйти за бюджет: это честно помеченное предложение доплатить
    stretch = req.budget_rub * COMFORT_STRETCH if req.budget_rub else None
    better = [p for p in all_options if p.hotel.id != optimum.hotel.id and p is not economy
              and p.total_rub >= optimum.total_rub * (1 + MIN_GAP) and _better(p.hotel, optimum.hotel)
              and (stretch is None or p.total_rub <= stretch)
              and _minutes(p) <= max(2 * COMFORT_TRANSFER_MIN, _minutes(optimum) + 30)]
    comfort = max(better, key=lambda p: (_class(p.hotel), -_sea(p.hotel), -p.total_rub)) if better else None

    packages = []
    for tier, pkg in (("economy", economy), ("optimum", optimum), ("comfort", comfort)):
        if pkg is not None:
            packages.append(pkg.model_copy(update={"id": tier, "label": LABELS[tier]}))
    for pkg in packages:
        if pkg.id != "optimum":
            pkg.tradeoffs = tradeoffs(pkg, optimum)
        pkg.issues = check(pkg, req)
    return packages


def _minutes(pkg: Package) -> int:
    return pkg.transfer.minutes if pkg.transfer else COMFORT_TRANSFER_MIN


def _fit(index: int, total: int, pkg: Package, req: Requirements) -> float:
    rank = 1 - min(index, OPTIMUM_WINDOW) / min(max(total, 1), OPTIMUM_WINDOW)
    weight = TRANSFER_WEIGHT_KIDS if req.children_ages else TRANSFER_WEIGHT
    return rank - max(0, _minutes(pkg) - COMFORT_TRANSFER_MIN) / 120 * weight


def check(pkg: Package, req: Requirements):
    """Проверка пакета теми же правилами, что и корзины."""
    trip = refresh(TripState(requirements=req, cart=items(pkg)))
    return trip.issues


# --- описание -------------------------------------------------------------------------------

def _class(hotel: HotelOption) -> int:
    return hotel.stars or 0


def _sea(hotel: HotelOption) -> int:
    return hotel.distance_to_sea_m if hotel.distance_to_sea_m is not None else 99_999


def _better(a: HotelOption, b: HotelOption) -> bool:
    """a заметно лучше b: выше класс или существенно ближе к морю."""
    if (a.stars or 0) > (b.stars or 0):
        return True
    return a.distance_to_sea_m is not None and b.distance_to_sea_m is not None \
        and a.distance_to_sea_m <= 400 < b.distance_to_sea_m


def title(hotel: HotelOption) -> str:
    return f"{hotel.stars}★ {KINDS[hotel.kind]}" if hotel.stars else KINDS[hotel.kind]


def _distance(m: int) -> str:
    return f"{m} м" if m < 1000 else f"{m / 1000:.1f} км".replace(".", ",")


def sea_text(hotel: HotelOption) -> str:
    if hotel.distance_to_sea_m is None:
        return "в горах" if HERE.in_mountains(hotel.district) else "море далеко"
    return f"до моря {_distance(hotel.distance_to_sea_m)}"


def highlights(hotel: HotelOption, transfer) -> list[str]:
    out = [f"{title(hotel)}, {hotel.district}", sea_text(hotel)]
    if transfer:
        out.append(f"{_from(transfer)} около {transfer.minutes} мин")
    if hotel.meal:
        out.append(MEALS[hotel.meal])
    if hotel.free_cancellation:
        out.append("бесплатная отмена")
    out += hotel.pros[:1]
    return out


def tradeoffs(pkg: Package, base: Package) -> list[str]:
    """Чем пакет отличается от Оптимума: разница в рублях и что за неё получаешь или теряешь."""
    a, b = pkg.hotel, base.hotel
    diff = pkg.total_rub - base.total_rub
    head = (f"дешевле Оптимума на {_rub(-diff)}" if diff < 0 else f"дороже Оптимума на {_rub(diff)}")
    facts = []
    if (a.stars or 0) != (b.stars or 0):
        facts.append(f"{title(a)} вместо {title(b)}")
    elif a.kind != b.kind:
        facts.append(f"{KINDS[a.kind]} вместо {KINDS[b.kind]}")
    if a.distance_to_sea_m != b.distance_to_sea_m and a.distance_to_sea_m is not None \
            and b.distance_to_sea_m is not None and abs(a.distance_to_sea_m - b.distance_to_sea_m) >= 150:
        facts.append(f"{sea_text(a)} вместо {_distance(b.distance_to_sea_m)}")
    elif (a.distance_to_sea_m is None) != (b.distance_to_sea_m is None):
        facts.append(f"{sea_text(a)}, а не у моря" if a.distance_to_sea_m is None
                     else f"у моря ({sea_text(a)}), а не {sea_text(b).replace('море далеко', 'вдали от моря')}")
    if pkg.transfer and base.transfer and abs(pkg.transfer.minutes - base.transfer.minutes) >= TRANSFER_DIFF_MIN:
        facts.append(f"дорога {_from(pkg.transfer)} {pkg.transfer.minutes} мин вместо {base.transfer.minutes}")
    if a.meal != b.meal and a.meal and b.meal:
        facts.append(f"{MEALS[a.meal]} вместо «{MEALS[b.meal]}»")
    if a.free_cancellation != b.free_cancellation:
        facts.append("есть бесплатная отмена" if a.free_cancellation else "без бесплатной отмены")
    if a.district != b.district:
        facts.append(f"район {a.district}")
    return [head + (": " + "; ".join(facts) if facts else "")]


def _from(transfer) -> str:
    return "из аэропорта" if transfer.start == "аэропорт" else "от " + transfer.start.replace("вокзал", "вокзала")


def describe(packages: list[Package]) -> str:
    lines = []
    for p in packages:
        flights = ""
        if p.outbound or p.back:
            legs = [f"{x.carrier_name} {x.from_airport}→{x.to_airport} {_local(x.depart_at):%d.%m %H:%M}→"
                    f"{_local(x.arrive_at):%d.%m %H:%M}" for x in (p.outbound, p.back) if x]
            first = p.outbound or p.back
            kind = f"Поезда (купе, {trains.source_note(first)})" if first.mode == "train" else "Перелёты"
            flights = f" {kind}: {'; '.join(legs)}, {_rub(p.flights_rub)} за всех."
        transfer = (f" Трансфер {_from(p.transfer)}: {p.transfer.minutes} мин, {_rub(p.transfer.price_rub)} туда-обратно."
                    if p.transfer else "")
        budget = "" if p.within_budget is not False else " Не укладывается в бюджет."
        line = (f"[{p.id}] {p.label}: {p.hotel.name} ({', '.join(p.highlights)}), отель {_rub(p.hotel.total_price_rub)}."
                f"{flights}{transfer} Итого {_rub(p.total_rub)}, на человека {_rub(p.per_person_rub)}.{budget}")
        if p.tradeoffs:
            line += " Сравнение: " + "; ".join(p.tradeoffs) + "."
        problems = [i.message for i in p.issues if i.severity in ("blocker", "warning")]
        if problems:
            line += " Проверка: " + " ".join(problems)
        lines.append(line)
    return "\n".join(lines)


# --- гибкие даты ----------------------------------------------------------------------------

def flex_dates(req: Requirements, hotel: HotelOption, out_prices: dict[date, FlightOption],
               back_prices: dict[date, FlightOption]) -> dict | None:
    """Сдвиг дат на ±FLEX_DAYS с той же длительностью, если он заметно дешевле.

    Сравниваем на одних и тех же данных (календарь самых дешёвых билетов и цена того же отеля),
    поэтому экономия считается честно, даже если календарь отличается от поиска на дату.
    """
    nights = (req.check_out - req.check_in).days
    guests = max(req.guests, 1)

    def cost(shift: int) -> int | None:
        start = req.check_in + timedelta(days=shift)
        end = start + timedelta(days=nights)
        out, back = out_prices.get(start), back_prices.get(end)
        if not out or not back or out.price_rub is None or back.price_rub is None:
            return None
        quote = providers.hotels().quote(hotel.id, start, end)
        if quote is None or not quote.available:
            return None
        return quote.total_price_rub + (out.price_rub + back.price_rub) * guests

    base = cost(0)
    if base is None:
        return None
    tomorrow = date.today() + timedelta(days=1)
    options = [(cost(d), d) for d in range(-FLEX_DAYS, FLEX_DAYS + 1)
               if d and req.check_in + timedelta(days=d) >= tomorrow]
    options = [(c, d) for c, d in options if c is not None]
    if not options:
        return None
    best, shift = min(options)
    saving = base - best
    if saving < max(FLEX_MIN_SAVING_RUB, base * FLEX_MIN_SAVING_SHARE):
        return None
    start = req.check_in + timedelta(days=shift)
    return {"shift_days": shift, "check_in": start.isoformat(),
            "check_out": (start + timedelta(days=nights)).isoformat(),
            "saving_rub": saving, "hotel": hotel.name}
