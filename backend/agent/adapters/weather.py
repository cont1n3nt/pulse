"""Погода и температура моря на даты поездки (Open-Meteo, без ключа, CC BY 4.0).

До 15 дней вперёд — прогноз. Дальше прогноза нет, поэтому показываем, какой погода была
в эти же даты год назад (архив), и подписываем это. Температура моря: морской прогноз
на ближайшие дни, дальше — многолетняя норма по месяцу.
"""

import logging
import time
from datetime import date, timedelta

import requests

from ..config import HTTP_TIMEOUT
from ..location import HERE

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_DAYS = 15
MARINE_DAYS = 7
ARCHIVE_LAG_DAYS = 5  # архив отстаёт от сегодняшнего дня на несколько дней
CACHE_TTL_S = 3600

# Точки погоды по районам (горы и побережье — разная погода), точка моря и норма температуры
# воды по месяцам — из location.json

# коды погоды WMO -> короткое описание
WEATHER_CODES = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман", 51: "морось", 53: "морось", 55: "морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с градом",
}

_cache: dict[tuple, tuple[float, object]] = {}


def _get(url: str, params: dict) -> dict | None:
    key = (url, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Open-Meteo недоступен: %s", type(exc).__name__)
        return None
    _cache[key] = (time.monotonic(), data)
    return data


def _years_back(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 февраля
        return day.replace(year=day.year - years, day=28)


def _days(data: dict | None, shift_years: int = 0) -> list[dict]:
    if not data or "daily" not in data:
        return []
    d = data["daily"]
    out = []
    for i, day in enumerate(d["time"]):
        t_max, t_min = d["temperature_2m_max"][i], d["temperature_2m_min"][i]
        if t_max is None or t_min is None:
            continue
        rain = d.get("precipitation_sum", [None] * len(d["time"]))[i]
        code = d.get("weather_code", [None] * len(d["time"]))[i]
        when = date.fromisoformat(day)
        if shift_years:
            when = _years_back(when, -shift_years)
        out.append({
            "date": when.isoformat(), "t_max": round(t_max), "t_min": round(t_min),
            "rain_mm": round(rain, 1) if rain is not None else None,
            "summary": WEATHER_CODES.get(code, "") if code is not None else
            ("дождь" if rain and rain >= 1 else "без осадков"),
        })
    return out


def _sea(start: date) -> dict | None:
    sea = HERE.sea
    if sea is None:
        return None
    today = date.today()
    if start <= today + timedelta(days=MARINE_DAYS - 1):
        data = _get(MARINE_URL, {"latitude": sea.lat, "longitude": sea.lon,
                                 "daily": "sea_surface_temperature_max", "timezone": HERE.timezone,
                                 "forecast_days": MARINE_DAYS})
        values = [v for v in (data or {}).get("daily", {}).get("sea_surface_temperature_max", []) if v is not None]
        if values:
            return {"t": round(sum(values) / len(values)), "source": "прогноз"}
    return {"t": sea.norm_by_month[start.month], "source": "норма для месяца"}


def trip_weather(district: str | None, start: date, end: date) -> dict | None:
    """Погода на дни поездки [start, end] и температура моря. None, если источник недоступен."""
    place = HERE.district(district) or HERE.district(HERE.default_district)
    today = date.today()
    params = {"latitude": place.lat, "longitude": place.lon, "timezone": HERE.timezone,
              "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code"}
    if end <= today + timedelta(days=FORECAST_DAYS):
        days = _days(_get(FORECAST_URL, {**params, "start_date": start.isoformat(),
                                         "end_date": end.isoformat()}))
        source = "прогноз Open-Meteo"
    else:
        # прогноза так далеко нет: те же даты год назад, честно подписанные
        years = 1
        while _years_back(end, years) > today - timedelta(days=ARCHIVE_LAG_DAYS):
            years += 1
        past_start, past_end = _years_back(start, years), _years_back(end, years)
        days = _days(_get(ARCHIVE_URL, {**params, "start_date": past_start.isoformat(),
                                        "end_date": past_end.isoformat()}), shift_years=years)
        source = f"так было в эти даты в {past_start.year} году (Open-Meteo)"
    if not days:
        return None
    sea = None if place.mountains else _sea(start)
    return {"district": place.name, "source": source, "days": days, "sea": sea}


def describe(weather: dict | None) -> str:
    if not weather:
        return "Погода: источник недоступен."
    days = weather["days"]
    t_max = round(sum(d["t_max"] for d in days) / len(days))
    t_min = round(sum(d["t_min"] for d in days) / len(days))
    rainy = sum(1 for d in days if (d["rain_mm"] or 0) >= 1)
    text = (f"Погода ({weather['source']}, {weather['district']}): днём около {t_max}°, ночью {t_min}°, "
            f"дождливых дней {rainy} из {len(days)}.")
    if weather.get("sea"):
        text += f" Море около {weather['sea']['t']}° ({weather['sea']['source']})."
    return text
