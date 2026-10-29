"""Общее для тестов: без сети, на данных локации по умолчанию (Сочи).

Тесты проверяют детерминированное ядро — пакеты, проверку поездки, перенос заказа — без модели
и без внешних API: настоящее расписание поездов отключено, перелёты собираются в тесте.
"""

import os
import tempfile
from datetime import date, datetime, timedelta

import pytest

# API-тесты импортируются вместе с чистым ядром: изолируем пути до первого импорта config.
_DATA = tempfile.TemporaryDirectory(prefix="pulse-pytest-", ignore_cleanup_errors=True)
os.environ.update(DATA_DIR=_DATA.name + "/agent", PULSE_DB_PATH=_DATA.name + "/app.sqlite3",
                  PULSE_AUTH_KEY="pytest-secret-" * 5, LLM_API_KEY="test-key-no-network",
                  LANGSMITH_TRACING="false")

from backend.agent.adapters import hotels as hotels_db
from backend.agent.adapters import rasp
from backend.agent.contracts import FlightOption

CHECK_IN = date(2027, 11, 20)  # межсезонье: отелей хватает, цены детерминированы
CHECK_OUT = date(2027, 11, 25)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(rasp, "enabled", lambda: False)


def flight(depart: datetime, hours: float = 3, price: int = 7000, outbound: bool = True,
           origin: str = "MOW") -> FlightOption:
    """Перелёт в одну сторону; время — местное время курорта."""
    depart = depart.replace(tzinfo=hotels_db.LOCAL_TZ)
    here = hotels_db.AIRPORTS[0]
    return FlightOption(
        id=f"test-{'to' if outbound else 'from'}-{depart:%Y%m%d%H%M}", source="seed", carrier_code="SU",
        carrier_name="Аэрофлот", number="SU 1000", from_airport=origin if outbound else here,
        to_airport=here if outbound else origin, depart_at=depart, arrive_at=depart + timedelta(hours=hours),
        duration_min=int(hours * 60), transfers=0, price_rub=price)


def morning_out(day: date = CHECK_IN, **kw) -> FlightOption:
    return flight(datetime.combine(day, datetime.min.time()).replace(hour=8), **kw)


def evening_back(day: date = CHECK_OUT, **kw) -> FlightOption:
    return flight(datetime.combine(day, datetime.min.time()).replace(hour=18), outbound=False, **kw)
