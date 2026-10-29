"""Поезда в локацию: альтернатива перелёту. Нет секции rail в location.json — нет поездов.

С ключом YANDEX_RASP_KEY поезда — настоящее расписание из API Яндекс Расписаний (rasp.py): номер,
отправление и прибытие на нужную станцию; цена — из API, если она там есть, иначе демо.
Без ключа или при сбое API — демо-модель, как цены отелей: время в пути ориентировочное для
направления (порядок реальных фирменных и скорых поездов), отправление, прибытие и цена купе
считаются детерминированно. Открытого API с ценами РЖД нет, а сайты билетов мы не парсим.

Станции локации (в Сочи — вдоль побережья, от Лазаревской до Адлера) и время от них до конечной
(hub) — в location.json. Пассажир выходит на станции, ближайшей к отелю, поэтому время прибытия
и такси до отеля зависят от отеля.
Контракт FlightOption с mode="train" один для обоих источников: source = yandex_rasp или rail_demo.
"""

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta

from ..contracts import FlightOption, HotelOption, TransferOption
from ..location import HERE
from . import hotels as hotels_db
from . import rasp

# Города отправления (код как у Aviasales): название и центр — по нему находится код города
# в Яндекс Расписаниях. Сколько ехать до локации — в location.json (rail.hours_from).
ORIGINS = {
    "KRR": ("Краснодар", 45.0355, 38.9753), "ROV": ("Ростов-на-Дону", 47.2357, 39.7015),
    "MRV": ("Минеральные Воды", 44.2087, 43.1381), "VOZ": ("Воронеж", 51.6608, 39.2003),
    "VOG": ("Волгоград", 48.7080, 44.5133), "MOW": ("Москва", 55.7558, 37.6173),
    "GOJ": ("Нижний Новгород", 56.3269, 44.0059), "LED": ("Санкт-Петербург", 59.9386, 30.3141),
    "KUF": ("Самара", 53.1959, 50.1002), "KZN": ("Казань", 55.7963, 49.1088),
    "UFA": ("Уфа", 54.7388, 55.9721), "SVX": ("Екатеринбург", 56.8389, 60.6057),
}
RAIL = HERE.rail
# город → (название, часов в пути до конечной станции локации)
ROUTES = {code: (ORIGINS[code][0], hours) for code, hours in RAIL.hours_from.items()} if RAIL else {}
CITY_COORDS = {code: ORIGINS[code][1:] for code in ROUTES}
# станции локации: координаты и сколько минут поезд идёт от неё до конечной
STATIONS = {s.name: (s.lat, s.lon, s.minutes_to_hub) for s in RAIL.stations} if RAIL else {}
HUB = RAIL.hub if RAIL else None
# районы, куда дорога идёт от конечной, а не от ближайшей по прямой станции (в Сочи — горы и Сириус)
VIA_HUB = tuple(RAIL.via_hub) if RAIL else ()
ARRIVE_AT = RAIL.arrive_at if RAIL else time(9, 30)  # прибытие на конечную «туда», плюс сдвиг по направлению
DEPART_AT = RAIL.depart_at if RAIL else time(16, 0)  # отправление «обратно»: после выезда из отеля
BOARDING_BUFFER_MIN = 30  # приехать на вокзал до отправления
EXIT_MIN = 10  # выйти с вокзала и сесть в такси
ROAD_FACTOR = 1.3  # дорога по городу длиннее прямой
# Демо-цена купе на одного: посадка + час в пути, летом дороже
FARE_BASE_RUB = 900
FARE_PER_HOUR_RUB = 260
SUMMER = {6: 1.35, 7: 1.5, 8: 1.5, 9: 1.2}


def route(code: str) -> tuple[str, float] | None:
    return ROUTES.get(code)


def _jitter(*parts) -> int:
    """Детерминированный сдвиг 0–119 мин: у разных направлений разное расписание."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16) % 120


def _fare(hours: float, day: date) -> int:
    return round((FARE_BASE_RUB + FARE_PER_HOUR_RUB * hours) * SUMMER.get(day.month, 1.0) / 10) * 10


def _option(code: str, city: str, depart: datetime, arrive: datetime, outbound: bool, station: str) -> FlightOption:
    minutes = int((arrive - depart).total_seconds() // 60)
    return FlightOption(
        id=f"rail-{code}-{'to' if outbound else 'from'}-{depart:%Y%m%d%H%M}-{station}",
        source="rail_demo", mode="train", carrier_code="RZD", carrier_name="Поезд",
        number="купе", from_airport=city if outbound else station, to_airport=station if outbound else city,
        depart_at=depart.replace(tzinfo=hotels_db.LOCAL_TZ), arrive_at=arrive.replace(tzinfo=hotels_db.LOCAL_TZ),
        duration_min=minutes, transfers=0, price_rub=_fare(minutes / 60, depart.date()),
    )


def legs(code: str, check_in: date, check_out: date, station: str | None = None) -> tuple[FlightOption, FlightOption] | None:
    """Поезд туда к дню заезда и обратно в день выезда: настоящее расписание, иначе демо.
    Станция по умолчанию — конечная."""
    station = station or HUB
    return live_legs(code, check_in, check_out, station) or demo_legs(code, check_in, check_out, station)


def live_legs(code: str, check_in: date, check_out: date, station: str) -> tuple[FlightOption, FlightOption] | None:
    """Поезда из Яндекс Расписаний до этой станции или None: нет ключа, сбой, нет поезда в эти дни."""
    found = route(code)
    if found is None or not rasp.enabled():
        return None
    city, hours = found
    city_code = rasp.settlement(*CITY_COORDS[code])
    station_code = rasp.station(station, *STATIONS[station][:2])
    if not city_code or not station_code:
        return None
    there = rasp.outbound(city_code, station_code, check_in, hours)
    home = rasp.back(station_code, city_code, check_out) if there else None
    if not (there and home):
        return None
    return (rasp.to_option(there, True, city, station, _fare),
            rasp.to_option(home, False, city, station, _fare))


def demo_legs(code: str, check_in: date, check_out: date, station: str | None = None) -> tuple[FlightOption, FlightOption] | None:
    """Демо: прибытие утром в день заезда и отправление днём в день выезда."""
    found = route(code)
    if found is None:
        return None
    city, hours = found
    station = station or HUB
    offset = STATIONS[station][2]
    arrive = datetime.combine(check_in, ARRIVE_AT) + timedelta(minutes=_jitter(code, "to") - offset)
    outbound = _option(code, city, arrive - timedelta(hours=hours) + timedelta(minutes=offset), arrive, True, station)
    depart = datetime.combine(check_out, DEPART_AT) + timedelta(minutes=_jitter(code, "from") + offset)
    back = _option(code, city, depart, depart + timedelta(hours=hours) - timedelta(minutes=offset), False, station)
    return outbound, back


def stations_by_distance(hotel: HotelOption) -> list[tuple[str, float]]:
    """[(станция, км по прямой)] от ближайшей к отелю."""
    names = [HUB] if hotel.district in VIA_HUB else list(STATIONS)
    return sorted(((name, _km(hotel.lat, hotel.lon, *STATIONS[name][:2])) for name in names), key=lambda x: x[1])


def nearest_station(hotel: HotelOption) -> tuple[str, float]:
    """(станция, км по прямой) — где выйти, чтобы ехать до отеля меньше всего."""
    return stations_by_distance(hotel)[0]


LIVE_STATIONS_TRIED = 3  # не каждый поезд стоит в Лоо и Дагомысе: пробуем следующие станции
_POOL = ThreadPoolExecutor(max_workers=LIVE_STATIONS_TRIED, thread_name_prefix="rasp")


def for_hotel(code: str, check_in: date, check_out: date, hotel: HotelOption, guests: int = 1):
    """Поезда до станции у этого отеля и такси от неё: (туда, обратно, трансфер) или None.

    С настоящим расписанием — ближайшая к отелю станция, где есть поезд в эти дни; иначе демо.
    """
    nearby = stations_by_distance(hotel)
    tried = nearby[:LIVE_STATIONS_TRIED]
    # станции запрашиваются параллельно: по очереди это 15–20 с холодного API, выбор — всё так же ближайшая
    if rasp.enabled():
        found_by_station = list(_POOL.map(lambda s: live_legs(code, check_in, check_out, s[0]), tried))
    else:
        found_by_station = [None] * len(tried)
    for (station, km), found in zip(tried, found_by_station):
        if found:
            return *found, station_transfer(hotel, station, km, guests)
    station, km = nearby[0]
    found = demo_legs(code, check_in, check_out, station)
    if found is None:
        return None
    return *found, station_transfer(hotel, station, km, guests)


def is_live(leg: FlightOption | None) -> bool:
    return leg is not None and leg.source == "yandex_rasp"


def source_note(leg: FlightOption | None) -> str:
    """Как назвать источник поезда в ответе: агент должен говорить честно."""
    if not is_live(leg):
        return "демо-расписание"
    return "расписание Яндекс Расписаний, цена купе — демо" if leg.price_is_demo else "расписание и цена — Яндекс Расписания"


def station_transfer(hotel: HotelOption, station: str, km: float, guests: int = 1) -> TransferOption:
    if station == HUB and hotel.airport_drive_min is not None:
        # конечная у аэропорта (в Сочи — вокзал Адлер в 3 км): настоящий маршрут из аэропорта
        road_km = hotel.airport_drive_km or round(km * ROAD_FACTOR, 1)
        minutes = round(hotel.airport_drive_min * hotels_db.TRAFFIC_FACTOR) + EXIT_MIN
    else:
        road_km = round(km * ROAD_FACTOR, 1)
        minutes = round(road_km / hotels_db.TRANSFER_SPEED_KMH * 60) + EXIT_MIN
    price, car = hotels_db.taxi_fare(road_km, guests)
    return TransferOption(hotel_id=hotel.id, minutes=minutes, km=road_km, price_rub=price, car=car,
                          start=f"вокзал {station}")


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))
