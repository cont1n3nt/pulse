"""Поиск отелей Большого Сочи.

Объекты, адреса, координаты, звёзды, сайты и факты о расположении — реальные, из OpenStreetMap
(seed/sochi/hotels.json, выгрузка scripts/fetch_osm_sochi.py, © участники OpenStreetMap, ODbL).
Цена, наличие, питание и условия отмены — демо-модель: считаются детерминированно, поэтому
повторный поиск с теми же условиями даёт те же цены.
"""

import hashlib
import json
from datetime import date, time, timedelta, timezone
from functools import lru_cache

from ..config import SEED_DIR
from ..contracts import HotelOption, TransferOption
from ..guardrails import sanitize_hotel

DISTRICTS = ("Адлер", "Центр", "Сириус", "Красная Поляна", "Хоста", "Лазаревское")
AMENITY_CODES = ("wifi", "pool", "parking", "kids", "pets", "breakfast", "beach_line", "spa")
AMENITY_NAMES = {"wifi": "Wi-Fi", "pool": "бассейн", "parking": "парковка", "kids": "для детей",
                 "pets": "с животными", "breakfast": "завтрак", "beach_line": "первая линия",
                 "spa": "спа"}

# параметры локации, нужные для проверки состыковок
LOCAL_TZ = timezone(timedelta(hours=3))  # Сочи, MSK
AIRPORTS = ("AER",)
CHECK_IN_TIME = time(14, 0)
CHECK_OUT_TIME = time(12, 0)
TRANSFER_SPEED_KMH = 35  # если маршрута нет: средняя скорость такси по Сочи с пробками
TRAFFIC_FACTOR = 1.3  # маршрут OSRM — без пробок, курортные дороги загружены
TRANSFER_OVERHEAD_MIN = 20  # выход из аэропорта, багаж, ожидание машины
# Демо-тариф такси аэропорт ⇄ отель (допущение, порядок цен Сочи): посадка + километры, минивэн дороже
TAXI_BASE_RUB = 500
TAXI_PER_KM_RUB = 38
MINIVAN_FACTOR = 1.5
MINIVAN_FROM_GUESTS = 5

# коэффициенты сезона по месяцам
_COAST = {1: 0.7, 2: 0.7, 3: 0.7, 4: 0.7, 5: 1.0, 6: 1.3, 7: 1.6, 8: 1.6,
          9: 1.3, 10: 1.0, 11: 0.7, 12: 0.8}
_MOUNTAIN = {1: 1.6, 2: 1.6, 3: 1.4, 4: 0.7, 5: 0.7, 6: 0.9, 7: 1.1, 8: 1.1,
             9: 0.9, 10: 0.7, 11: 0.7, 12: 1.6}

# Демо-модель цены за ночь в межсезонье (коэффициент сезона 1.0). Допущения, а не статистика:
# порядок цен для Сочи, чтобы выдача и бюджеты выглядели правдоподобно. Класс — звёзды из OSM,
# а где их нет — оценка выгрузки по типу объекта и известным сетям (price_class).
BASE_PRICE_BY_CLASS = {1: 1800, 2: 3200, 3: 5000, 4: 8500, 5: 16000}
KIND_FACTOR = {"hostel": 0.8, "guest_house": 0.95, "apartment": 1.0, "hotel": 1.0, "resort": 1.15}
DISTRICT_FACTOR = {"Центр": 1.1, "Сириус": 1.15, "Красная Поляна": 1.1, "Адлер": 1.0,
                   "Хоста": 0.95, "Лазаревское": 0.85}


def sea_factor(distance_m: int | None) -> float:
    if distance_m is None:
        return 1.0  # горы: море не влияет
    if distance_m <= 300:
        return 1.2
    if distance_m <= 800:
        return 1.05
    return 0.85 if distance_m > 2000 else 1.0


def base_price(raw: dict) -> int:
    """Базовая цена объекта: класс × тип × район × близость к морю × индивидуальный разброс ±15%."""
    price = (BASE_PRICE_BY_CLASS[raw.get("price_class") or 3] * KIND_FACTOR[raw["kind"]]
             * DISTRICT_FACTOR.get(raw["district"], 1.0) * sea_factor(raw.get("distance_to_sea_m"))
             * (0.85 + 0.3 * h(raw["id"], "base")))
    return round(price / 100) * 100


@lru_cache
def _load() -> tuple[dict, ...]:
    with open(SEED_DIR / "sochi" / "hotels.json", encoding="utf-8") as f:
        hotels = [sanitize_hotel(raw) for raw in json.load(f)]
    for raw in hotels:
        raw["price_from_rub"] = base_price(raw)
    return tuple(hotels)


def h(*parts) -> float:
    """Детерминированное число 0..1 по набору значений."""
    digest = hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def price_per_night(hotel: dict, day: date) -> int:
    season = (_MOUNTAIN if hotel["district"] == "Красная Поляна" else _COAST)[day.month]
    weekend = 1.1 if day.weekday() in (4, 5) else 1.0  # пт/сб
    noise = 0.9 + 0.2 * h(hotel["id"], day)
    return round(hotel["price_from_rub"] * season * weekend * noise / 10) * 10


def available(hotel: dict, check_in: date, check_out: date) -> bool:
    high = check_in.month in (7, 8) or (check_in.month, check_in.day) >= (12, 25) \
        or (check_in.month, check_in.day) <= (1, 8)
    load = 0.35 if high else 0.1
    return h(hotel["id"], check_in, "avail") >= load


def _meal(hotel: dict) -> str:
    # тариф согласован с удобством «завтрак», иначе агент получает противоречивые факты
    breakfast = hotel["amenities"].get("breakfast")
    if breakfast is False:
        return "none"
    r = h(hotel["id"], "meal")
    stars = hotel.get("stars")
    if breakfast is None:
        if hotel["kind"] in ("hostel", "apartment"):
            return "none"
        if hotel["kind"] == "guest_house":
            return "none" if r < 0.6 else "breakfast"
        return "breakfast" if r < 0.7 else "none"
    if stars is not None and stars >= 4 and hotel["kind"] in ("hotel", "resort"):
        return "breakfast" if r < 0.5 else "half_board" if r < 0.8 else "all_inclusive"
    return "breakfast"


def _free_cancellation(hotel: dict) -> bool:
    stars = hotel.get("stars")
    return h(hotel["id"], "cancel") < (0.7 if stars is not None and stars >= 4 else 0.4)


def _priced(raw: dict, check_in: date, check_out: date) -> HotelOption:
    nights = (check_out - check_in).days
    total = sum(price_per_night(raw, check_in + timedelta(days=i)) for i in range(nights))
    return HotelOption.model_validate(raw).model_copy(update={
        "check_in": check_in, "check_out": check_out, "nights": nights,
        "price_per_night_rub": round(total / nights), "total_price_rub": total,
        "available": available(raw, check_in, check_out),
        "meal": _meal(raw), "free_cancellation": _free_cancellation(raw),
    })


def search(check_in: date, check_out: date, district: str | None = None,
           max_price_per_night: int | None = None, must_have=(), near_sea: bool = False,
           exclude_kinds=(), limit: int = 5, diversify: bool = True,
           target_per_night: int | None = None) -> list[HotelOption]:
    """Отели под условия в порядке ранжирования.

    max_price_per_night отсекает дороже лимита. target_per_night только сдвигает ориентир цены
    в ранжировании, ничего не отсекая: для пакетов, где бюджет проверяется по итогу поездки.
    """
    found = []
    for raw in _load():
        if district and raw["district"].lower() != district.lower():
            continue
        if raw["kind"] in exclude_kinds:
            continue
        if not available(raw, check_in, check_out):
            continue
        if any(raw["amenities"].get(code) is False for code in must_have):
            continue
        sea = raw["distance_to_sea_m"]
        if near_sea and (sea is None or sea > 500):
            continue
        hotel = _priced(raw, check_in, check_out)
        if max_price_per_night is not None and hotel.price_per_night_rub > max_price_per_night:
            continue
        unconfirmed = any(raw["amenities"].get(code) is not True for code in must_have)
        found.append((raw, hotel, unconfirmed))
    if not found:
        return []
    prices = sorted(hotel.price_per_night_rub for _, hotel, _ in found)
    # ориентир цены: большая часть лимита за ночь, а без лимита — середина выдачи
    limit_hint = max_price_per_night or target_per_night
    target = 0.7 * limit_hint if limit_hint else prices[len(prices) // 2]
    ranked = sorted(found, key=lambda t: score(t[0], t[1].price_per_night_rub, target, t[2]), reverse=True)
    hotels = [hotel for _, hotel, _ in ranked]
    return diverse(hotels, limit) if diversify else hotels[:limit]


# Порядок выдачи. Самые дешёвые первыми — это всегда хостелы и безымянные комнаты, даже при
# большом бюджете. Поэтому: цена ближе к ориентиру, выше класс, больше известно, ближе к морю.
SCORE_WEIGHTS = {"price": 0.45, "class": 0.25, "known": 0.15, "sea": 0.15}
UNCONFIRMED_PENALTY = 0.2  # обязательное удобство не подтверждено картой
HOSTEL_PENALTY = 0.25  # общие комнаты — только если ничего другого нет по цене
MAX_SAME_KIND = 2


def completeness(raw: dict) -> int:
    """Сколько об объекте известно из OSM (0–4): о таких можно рассказать и их можно проверить."""
    return sum((bool(raw.get("address")), bool(raw.get("website")), bool(raw.get("stars")),
                raw["amenities"].get("wifi") is not None))


def sea_score(distance_m: int | None) -> float:
    if distance_m is None:
        return 0.5  # горы: близость к морю не про них
    if distance_m <= 300:
        return 1.0
    if distance_m <= 800:
        return 0.7
    return 0.4 if distance_m <= 2000 else 0.1


def score(raw: dict, price: int, target: float, unconfirmed: bool) -> float:
    price_fit = max(0.0, 1 - abs(price - target) / target)
    value = (SCORE_WEIGHTS["price"] * price_fit
             + SCORE_WEIGHTS["class"] * (raw.get("price_class") or 3) / 5
             + SCORE_WEIGHTS["known"] * completeness(raw) / 4
             + SCORE_WEIGHTS["sea"] * sea_score(raw.get("distance_to_sea_m")))
    if unconfirmed:
        value -= UNCONFIRMED_PENALTY
    if raw["kind"] == "hostel":
        value -= HOSTEL_PENALTY
    return value


def diverse(ranked: list[HotelOption], limit: int) -> list[HotelOption]:
    """Не больше двух объектов одного типа, пока есть из чего выбрать: пять гостевых домов
    подряд — не сравнение."""
    picked, rest = [], []
    for hotel in ranked:
        if sum(p.kind == hotel.kind for p in picked) < MAX_SAME_KIND:
            picked.append(hotel)
        else:
            rest.append(hotel)
        if len(picked) == limit:
            return picked
    return picked + rest[:limit - len(picked)]


def quote(hotel_id: str, check_in: date, check_out: date) -> HotelOption | None:
    """Актуальная цена и наличие отеля на даты: для перепроверки корзины."""
    for raw in _load():
        if raw["id"] == hotel_id:
            return _priced(raw, check_in, check_out)
    return None


def get(hotel_id: str) -> HotelOption | None:
    for raw in _load():
        if raw["id"] == hotel_id:
            return HotelOption.model_validate(raw)
    return None


def transfer_minutes(hotel: HotelOption) -> int | None:
    """Дорога от аэропорта до отеля с выходом из аэропорта: маршрут по дорогам OSM с запасом
    на пробки, а без маршрута — по прямой со средней скоростью."""
    if hotel.airport_drive_min is not None:
        return round(hotel.airport_drive_min * TRAFFIC_FACTOR) + TRANSFER_OVERHEAD_MIN
    if hotel.distance_to_airport_km is None:
        return None
    return round(hotel.distance_to_airport_km / TRANSFER_SPEED_KMH * 60) + TRANSFER_OVERHEAD_MIN


def transfer(hotel: HotelOption, guests: int) -> TransferOption | None:
    """Такси аэропорт → отель и обратно: время реальное по дорогам, цена — демо-тариф."""
    minutes = transfer_minutes(hotel)
    km = hotel.airport_drive_km or hotel.distance_to_airport_km
    if minutes is None or km is None:
        return None
    minivan = guests >= MINIVAN_FROM_GUESTS
    one_way = (TAXI_BASE_RUB + TAXI_PER_KM_RUB * km) * (MINIVAN_FACTOR if minivan else 1)
    return TransferOption(hotel_id=hotel.id, minutes=minutes, km=km, price_rub=round(one_way / 50) * 50 * 2,
                          car="minivan" if minivan else "standard")
