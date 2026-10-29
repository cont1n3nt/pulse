"""Цены на перелёты из кэша Aviasales (Travelpayouts Data API).

Это не живой поиск: кэш хранит самые дешёвые билеты, которые пользователи Aviasales находили
недавно. Поэтому на конкретную дату обычно есть один вариант, а иногда ни одного.
"""

import logging
import time
from datetime import date, datetime, timedelta

import requests

from ..config import HTTP_TIMEOUT, TRAVELPAYOUTS_TOKEN
from ..contracts import FlightOption, SourceUnavailable
from ..reference import airlines
from .hotels import LOCAL_TZ

log = logging.getLogger(__name__)

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
GROUPED_URL = "https://api.travelpayouts.com/aviasales/v3/grouped_prices"
CACHE_TTL_S = 1800  # кэш Aviasales сам обновляется не чаще, лишние запросы не нужны

_cache: dict[tuple, tuple[float, object]] = {}


def to_flight(raw: dict) -> FlightOption:
    depart = datetime.fromisoformat(raw["departure_at"])
    duration = int(raw.get("duration_to") or raw.get("duration") or 0)
    code = raw["airline"]
    return FlightOption(
        id=f'av-{code}{raw["flight_number"]}-{depart:%Y%m%d%H%M}',
        source="aviasales",
        carrier_code=code,
        carrier_name=airlines().get(code, code),
        number=f'{code} {raw["flight_number"]}',
        from_airport=raw["origin_airport"],
        to_airport=raw["destination_airport"],
        depart_at=depart,
        arrive_at=depart + timedelta(minutes=duration),
        duration_min=duration,
        transfers=int(raw.get("transfers", 0)),
        price_rub=raw.get("price"),
        link="https://www.aviasales.ru" + raw["link"] if raw.get("link") else None,
    )


def _get(url: str, params: dict):
    key = (url, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                            headers={"X-Access-Token": TRAVELPAYOUTS_TOKEN})
        resp.raise_for_status()
        data = resp.json().get("data")
    except (requests.RequestException, ValueError) as exc:
        log.warning("Aviasales недоступен: %s", type(exc).__name__)
        raise SourceUnavailable(type(exc).__name__) from exc
    _cache[key] = (time.monotonic(), data)
    return data


def fetch_flights(origin: str, destination: str, depart: str,
                  return_date: str | None = None, limit: int = 5) -> list[FlightOption]:
    params = {
        "origin": origin, "destination": destination, "departure_at": depart,
        "currency": "rub", "sorting": "price", "limit": max(1, min(limit, 10)), "one_way": "true",
    }
    if return_date:
        params["return_at"] = return_date
    return [to_flight(t) for t in _get(URL, params) or []]


def month_prices(origin: str, destination: str, month: str) -> dict[date, FlightOption]:
    """Самый дешёвый билет на каждый день месяца YYYY-MM: для гибких дат."""
    data = _get(GROUPED_URL, {"origin": origin, "destination": destination, "departure_at": month,
                              "currency": "rub", "group_by": "departure_at"}) or {}
    return {date.fromisoformat(day): to_flight(raw) for day, raw in data.items()}


def cheapest_on(origin: str, destination: str, day: date, arrive_by: date | None = None) -> FlightOption | None:
    """Самый дешёвый билет на дату: сначала поиск по дате, затем календарь месяца.

    arrive_by: дата заезда. Ночной рейс с прилётом на следующий день сжёг бы оплаченную ночь,
    поэтому сначала ищем среди тех, что прилетают вовремя, и только без них берём самый дешёвый.
    """
    found = fetch_flights(origin, destination, day.isoformat(), limit=10 if arrive_by else 1)
    on_time = [f for f in found if arrive_by is None or _local_day(f.arrive_at) <= arrive_by]
    if on_time or found:
        return (on_time or found)[0]
    return month_prices(origin, destination, f"{day:%Y-%m}").get(day)


def _local_day(moment: datetime) -> date:
    return (moment.astimezone(LOCAL_TZ) if moment.tzinfo else moment).date()


def prices_around(origin: str, destination: str, day: date, days: int) -> dict[date, FlightOption]:
    """Цены на дни day±days из календарей нужных месяцев."""
    months = {f"{day + timedelta(days=d):%Y-%m}" for d in range(-days, days + 1)}
    found: dict[date, FlightOption] = {}
    for month in months:
        found |= month_prices(origin, destination, month)
    return {d: f for d, f in found.items() if abs((d - day).days) <= days}
