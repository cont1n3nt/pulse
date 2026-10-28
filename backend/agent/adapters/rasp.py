"""Настоящее расписание поездов из API Яндекс Расписаний (https://yandex.ru/dev/rasp/doc/ru/).

Ключ — бесплатный, в кабинете разработчика Яндекса (YANDEX_RASP_KEY). Без ключа или при сбое API
функции возвращают None, и trains.py строит демо-расписание. Цены в API бывают не у всех поездов:
тогда цена купе — демо (price_is_demo). По условиям API источник надо указывать: «Яндекс Расписания».
"""

import logging
import math
import time
from datetime import date, datetime, timedelta

import requests

from ..config import YANDEX_RASP_KEY
from ..contracts import FlightOption
from .hotels import LOCAL_TZ

log = logging.getLogger(__name__)

URL = "https://api.rasp.yandex-net.ru/v3.0"  # api.rasp.yandex.net документация называет ненадёжным
TIMEOUT_S = 6  # расписание — дополнение к поездке: медленный API не должен тормозить ответ
CACHE_TTL_S = 6 * 3600  # расписание меняется редко
PAUSE_S = 600  # после сбоя не дёргаем API 10 минут, сразу демо
CHECK_IN_HOUR = 14  # туда: последний поезд, прибывающий до заселения
CHECK_OUT_HOUR = 13  # обратно: первый поезд после выезда из отеля и дороги до вокзала
# Время в пути в trains.ROUTES — оценка; настоящие поезда бывают в 1,5 раза дольше (Москва — 26–39 ч)
SLOW_FACTOR = 1.6

_cache: dict[tuple, tuple[float, object]] = {}
_codes: dict[tuple, str | None] = {}
_paused_until = 0.0


def enabled() -> bool:
    return bool(YANDEX_RASP_KEY) and time.monotonic() >= _paused_until


def _get(method: str, params: dict):
    global _paused_until
    key = (method, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        resp = requests.get(f"{URL}/{method}/", timeout=TIMEOUT_S,
                            params={"format": "json", "lang": "ru_RU", **params},
                            headers={"Authorization": YANDEX_RASP_KEY})
        if resp.status_code == 404:  # неизвестный код станции: ответ «нет», а не сбой
            data = {}
        else:
            resp.raise_for_status()
            data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Яндекс Расписания недоступны: %s", type(exc).__name__)
        _paused_until = time.monotonic() + PAUSE_S
        return None
    _cache[key] = (time.monotonic(), data)
    return data


def settlement(lat: float, lon: float) -> str | None:
    """Код города Яндекс Расписаний по координатам центра."""
    key = ("c", lat, lon)
    if key not in _codes:
        data = _get("nearest_settlement", {"lat": lat, "lng": lon, "distance": 30})
        if data is None:
            return None
        _codes[key] = data.get("code")
    return _codes[key]


def station(name: str, lat: float, lon: float) -> str | None:
    """Код железнодорожной станции: по названию среди ближайших, иначе ближайшая."""
    key = ("s", name)
    if key not in _codes:
        data = _get("nearest_stations", {"lat": lat, "lng": lon, "distance": 3, "transport_types": "train"})
        if data is None:
            return None
        found = sorted(data.get("stations") or [], key=lambda s: s.get("distance", 99))
        named = [s for s in found if name.lower() in (s.get("title") or "").lower()]
        _codes[key] = (named or found or [{}])[0].get("code")
    return _codes[key]


def _segments(start: str, end: str, day: date) -> list[dict] | None:
    data = _get("search", {"from": start, "to": end, "date": day.isoformat(), "transport_types": "train",
                           "limit": 100})
    return None if data is None else data.get("segments") or []


def _local(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(LOCAL_TZ)


def outbound(city: str, station_code: str, check_in: date, hours: float) -> dict | None:
    """Поезд с прибытием в день заезда: последний до заселения, иначе самый ранний после.

    Дальние поезда ходят не каждый день. Если в день заезда прибытия нет, берём ближайшее
    к заселению: настоящий поезд с честной проблемой в проверке (лишняя или потерянная ночь)
    лучше выдуманного демо-поезда.
    """
    found = []
    for back_days in range(math.ceil(hours * SLOW_FACTOR / 24) + 1):
        segments = _segments(city, station_code, check_in - timedelta(days=back_days))
        if segments is None:
            return None
        found += segments
    on_day = [s for s in found if _local(s["arrival"]).date() == check_in]
    before = [s for s in on_day if _local(s["arrival"]).hour < CHECK_IN_HOUR]
    if before:
        return max(before, key=lambda s: _local(s["arrival"]))
    if on_day:
        return min(on_day, key=lambda s: _local(s["arrival"]))
    settle = datetime.combine(check_in, datetime.min.time().replace(hour=CHECK_IN_HOUR), LOCAL_TZ)
    return min(found, key=lambda s: abs(_local(s["arrival"]) - settle), default=None)


def back(station_code: str, city: str, check_out: date) -> dict | None:
    """Поезд в день выезда: первый после выезда из отеля, иначе последний до него.

    В день выезда поезда нет — последний накануне (ночь в отеле не используется),
    иначе первый на следующий день.
    """
    segments = _segments(station_code, city, check_out)
    if segments is None:
        return None
    if segments:
        after = [s for s in segments if _local(s["departure"]).hour >= CHECK_OUT_HOUR]
        if after:
            return min(after, key=lambda s: _local(s["departure"]))
        return max(segments, key=lambda s: _local(s["departure"]))
    earlier = _segments(station_code, city, check_out - timedelta(days=1)) or []
    if earlier:
        return max(earlier, key=lambda s: _local(s["departure"]))
    later = _segments(station_code, city, check_out + timedelta(days=1)) or []
    return min(later, key=lambda s: _local(s["departure"]), default=None)


def to_option(segment: dict, outbound_leg: bool, city_name: str, station_name: str,
              demo_fare) -> FlightOption:
    thread = segment.get("thread") or {}
    depart, arrive = datetime.fromisoformat(segment["departure"]), datetime.fromisoformat(segment["arrival"])
    minutes = int((arrive - depart).total_seconds() // 60)
    prices = [p["price"]["whole"] for p in (segment.get("tickets_info") or {}).get("place") or []
              if (p.get("price") or {}).get("whole")]
    number = thread.get("number") or "поезд"
    return FlightOption(
        id=f"rasp-{number}-{depart:%Y%m%d%H%M}-{station_name}",
        source="yandex_rasp", mode="train", carrier_code="RZD",
        carrier_name=(thread.get("carrier") or {}).get("title") or "РЖД",
        number=f"№ {number}", title=thread.get("title"),
        from_airport=city_name if outbound_leg else station_name,
        to_airport=station_name if outbound_leg else city_name,
        depart_at=depart, arrive_at=arrive, duration_min=minutes, transfers=0,
        # в API бывает несколько классов: берём самый дешёвый, он ближе к купе, чем СВ
        price_rub=min(prices) if prices else demo_fare(minutes / 60, depart.date()),
        price_is_demo=not prices,
    )
