"""Поезда в Большой Сочи: альтернатива перелёту.

С ключом YANDEX_RASP_KEY поезда — настоящее расписание из API Яндекс Расписаний (rasp.py): номер,
отправление и прибытие на нужную станцию; цена — из API, если она там есть, иначе демо.
Без ключа или при сбое API — демо-модель, как цены отелей: время в пути ориентировочное для
направления (порядок реальных фирменных и скорых поездов), отправление, прибытие и цена купе
считаются детерминированно. Открытого API с ценами РЖД нет, а сайты билетов мы не парсим.

Поезда идут вдоль побережья: Лазаревская → Лоо → Дагомыс → Сочи → Хоста → Адлер. Пассажир выходит на станции,
ближайшей к отелю, поэтому время прибытия и такси до отеля зависят от отеля.
Контракт FlightOption с mode="train" один для обоих источников: source = yandex_rasp или rail_demo.
"""

import hashlib
import math
from datetime import date, datetime, time, timedelta

from ..contracts import FlightOption, HotelOption, TransferOption
from . import hotels as hotels_db
from . import rasp

# город (код как у Aviasales) → (название, часов в пути до Адлера)
ROUTES = {
    "KRR": ("Краснодар", 5.5), "ROV": ("Ростов-на-Дону", 11), "MRV": ("Минеральные Воды", 14),
    "VOZ": ("Воронеж", 19), "VOG": ("Волгоград", 22), "MOW": ("Москва", 25),
    "GOJ": ("Нижний Новгород", 34), "LED": ("Санкт-Петербург", 38), "KUF": ("Самара", 40),
    "KZN": ("Казань", 42), "UFA": ("Уфа", 52), "SVX": ("Екатеринбург", 60),
}
# центры городов: по ним находится код города в Яндекс Расписаниях
CITY_COORDS = {
    "KRR": (45.0355, 38.9753), "ROV": (47.2357, 39.7015), "MRV": (44.2087, 43.1381),
    "VOZ": (51.6608, 39.2003), "VOG": (48.7080, 44.5133), "MOW": (55.7558, 37.6173),
    "GOJ": (56.3269, 44.0059), "LED": (59.9386, 30.3141), "KUF": (53.1959, 50.1002),
    "KZN": (55.7963, 49.1088), "UFA": (54.7388, 55.9721), "SVX": (56.8389, 60.6057),
}
# станции вдоль побережья: координаты и сколько минут поезд идёт от неё до Адлера
STATIONS = {
    "Лазаревская": (43.9097, 39.3331, 150),
    "Лоо": (43.7072, 39.5847, 100),
    "Дагомыс": (43.6585, 39.6530, 85),
    "Сочи": (43.5917, 39.7256, 60),
    "Хоста": (43.5145, 39.8689, 30),
    "Адлер": (43.4383, 39.9180, 0),
}
# дорога в горы и в Сириус идёт через Адлер, а не от ближайшей по прямой станции
VIA_ADLER = ("Красная Поляна", "Сириус")
ARRIVE_AT = time(9, 30)  # прибытие в Адлер «туда», плюс сдвиг по направлению
DEPART_AT = time(16, 0)  # отправление из Адлера «обратно»: после выезда из отеля в 12:00
BOARDING_BUFFER_MIN = 30  # приехать на вокзал до отправления
EXIT_MIN = 10  # выйти с вокзала и сесть в такси
ROAD_FACTOR = 1.3  # дорога по Сочи длиннее прямой
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


def legs(code: str, check_in: date, check_out: date, station: str = "Адлер") -> tuple[FlightOption, FlightOption] | None:
    """Поезд туда к дню заезда и обратно в день выезда: настоящее расписание, иначе демо."""
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


def demo_legs(code: str, check_in: date, check_out: date, station: str = "Адлер") -> tuple[FlightOption, FlightOption] | None:
    """Демо: прибытие утром в день заезда и отправление днём в день выезда."""
    found = route(code)
    if found is None:
        return None
    city, hours = found
    offset = STATIONS[station][2]
    arrive = datetime.combine(check_in, ARRIVE_AT) + timedelta(minutes=_jitter(code, "to") - offset)
    outbound = _option(code, city, arrive - timedelta(hours=hours) + timedelta(minutes=offset), arrive, True, station)
    depart = datetime.combine(check_out, DEPART_AT) + timedelta(minutes=_jitter(code, "from") + offset)
    back = _option(code, city, depart, depart + timedelta(hours=hours) - timedelta(minutes=offset), False, station)
    return outbound, back


def stations_by_distance(hotel: HotelOption) -> list[tuple[str, float]]:
    """[(станция, км по прямой)] от ближайшей к отелю."""
    names = ["Адлер"] if hotel.district in VIA_ADLER else list(STATIONS)
    return sorted(((name, _km(hotel.lat, hotel.lon, *STATIONS[name][:2])) for name in names), key=lambda x: x[1])


def nearest_station(hotel: HotelOption) -> tuple[str, float]:
    """(станция, км по прямой) — где выйти, чтобы ехать до отеля меньше всего."""
    return stations_by_distance(hotel)[0]


LIVE_STATIONS_TRIED = 3  # не каждый поезд стоит в Лоо и Дагомысе: пробуем следующие станции


def for_hotel(code: str, check_in: date, check_out: date, hotel: HotelOption, guests: int = 1):
    """Поезда до станции у этого отеля и такси от неё: (туда, обратно, трансфер) или None.

    С настоящим расписанием — ближайшая к отелю станция, где есть поезд в эти дни; иначе демо.
    """
    nearby = stations_by_distance(hotel)
    for station, km in nearby[:LIVE_STATIONS_TRIED]:
        found = live_legs(code, check_in, check_out, station)
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
    if station == "Адлер" and hotel.airport_drive_min is not None:
        # вокзал Адлер в 3 км от аэропорта: берём настоящий маршрут по дорогам из аэропорта
        road_km = hotel.airport_drive_km or round(km * ROAD_FACTOR, 1)
        minutes = round(hotel.airport_drive_min * hotels_db.TRAFFIC_FACTOR) + EXIT_MIN
    else:
        road_km = round(km * ROAD_FACTOR, 1)
        minutes = round(road_km / hotels_db.TRANSFER_SPEED_KMH * 60) + EXIT_MIN
    minivan = guests >= hotels_db.MINIVAN_FROM_GUESTS
    one_way = (hotels_db.TAXI_BASE_RUB + hotels_db.TAXI_PER_KM_RUB * road_km) * (hotels_db.MINIVAN_FACTOR if minivan else 1)
    return TransferOption(hotel_id=hotel.id, minutes=minutes, km=road_km, price_rub=round(one_way / 50) * 50 * 2,
                          car="minivan" if minivan else "standard", start=f"вокзал {station}")


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))
