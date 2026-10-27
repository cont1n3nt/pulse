"""Моковый поиск отелей Большого Сочи: цены, наличие, питание и отмена считаются детерминированно."""

import hashlib
import json
from datetime import date, time, timedelta, timezone
from functools import lru_cache

from ..config import SEED_DIR
from ..contracts import HotelOption
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
TRANSFER_SPEED_KMH = 35  # средняя скорость такси по Сочи с пробками
TRANSFER_OVERHEAD_MIN = 30  # выход из аэропорта, багаж, ожидание машины

# коэффициенты сезона по месяцам
_COAST = {1: 0.7, 2: 0.7, 3: 0.7, 4: 0.7, 5: 1.0, 6: 1.3, 7: 1.6, 8: 1.6,
          9: 1.3, 10: 1.0, 11: 0.7, 12: 0.8}
_MOUNTAIN = {1: 1.6, 2: 1.6, 3: 1.4, 4: 0.7, 5: 0.7, 6: 0.9, 7: 1.1, 8: 1.1,
             9: 0.9, 10: 0.7, 11: 0.7, 12: 1.6}


@lru_cache
def _load() -> tuple[dict, ...]:
    with open(SEED_DIR / "sochi" / "hotels.json", encoding="utf-8") as f:
        return tuple(sanitize_hotel(raw) for raw in json.load(f))


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
           exclude_kinds=(), limit: int = 5) -> list[HotelOption]:
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
        found.append((unconfirmed, hotel.price_per_night_rub, hotel))
    found.sort(key=lambda t: (t[0], t[1]))
    return [hotel for _, _, hotel in found[:limit]]


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
    """Оценка дороги от аэропорта до отеля, включая выход из аэропорта."""
    if hotel.distance_to_airport_km is None:
        return None
    return round(hotel.distance_to_airport_km / TRANSFER_SPEED_KMH * 60) + TRANSFER_OVERHEAD_MIN
