import logging
from datetime import datetime, timedelta

import requests

from ..config import HTTP_TIMEOUT, TRAVELPAYOUTS_TOKEN
from ..contracts import FlightOption
from ..reference import airlines

log = logging.getLogger(__name__)

URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class SourceUnavailable(Exception):
    pass


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


def fetch_flights(origin: str, destination: str, depart: str,
                  return_date: str | None = None, limit: int = 5) -> list[FlightOption]:
    params = {
        "origin": origin, "destination": destination, "departure_at": depart,
        "currency": "rub", "sorting": "price", "limit": max(1, min(limit, 10)),
    }
    if return_date:
        params["return_at"] = return_date
    try:
        resp = requests.get(URL, params=params, timeout=HTTP_TIMEOUT,
                            headers={"X-Access-Token": TRAVELPAYOUTS_TOKEN})
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException as exc:
        log.warning("Aviasales недоступен: %s", type(exc).__name__)
        raise SourceUnavailable(type(exc).__name__) from exc
    return [to_flight(t) for t in data]
