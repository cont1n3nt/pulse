"""Производственный календарь РФ: длинные выходные для «на майские», «на новогодние».

Источник — isdayoff.ru (учитывает переносы выходных). Год кэшируется на диске; если сервис
недоступен, считаем по субботам, воскресеньям и фиксированным праздникам без переносов.
"""

import logging
from datetime import date, timedelta

import requests

from ..config import DATA_DIR, HTTP_TIMEOUT

log = logging.getLogger(__name__)

URL = "https://isdayoff.ru/api/getdata"
CACHE_DIR = DATA_DIR / "calendar"
MIN_DAYS = 3  # выходные короче — обычные, не «длинные»

HOLIDAYS = {  # (месяц, день) -> название периода
    (1, 1): "новогодние праздники", (1, 7): "новогодние праздники",
    (2, 23): "23 февраля", (3, 8): "8 марта", (5, 1): "майские праздники",
    (5, 9): "День Победы", (6, 12): "День России", (11, 4): "День народного единства",
}


def _fallback_year(year: int) -> str:
    start = date(year, 1, 1)
    days = (date(year + 1, 1, 1) - start).days
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        off = d.weekday() >= 5 or (d.month, d.day) in HOLIDAYS or (d.month == 1 and d.day <= 8)
        out.append("1" if off else "0")
    return "".join(out)


def _year(year: int) -> tuple[str, bool]:
    """Строка из 0/1 на каждый день года и признак, что это официальный календарь."""
    path = CACHE_DIR / f"{year}.txt"
    if path.exists():
        return path.read_text(encoding="ascii"), True
    try:
        resp = requests.get(URL, params={"year": year, "cc": "ru"}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text.isdigit() or len(text) < 365:
            raise ValueError("неожиданный ответ")
    except (requests.RequestException, ValueError) as exc:
        log.warning("Календарь isdayoff недоступен (%s), считаю без переносов", type(exc).__name__)
        return _fallback_year(year), False
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")
    return text, True


def long_weekends(start: date, months: int = 12) -> tuple[list[dict], bool]:
    """Длинные выходные с праздником с даты start на months месяцев вперёд."""
    end = start + timedelta(days=31 * months)
    official = True
    days_off: list[date] = []
    for year in range(start.year, end.year + 1):
        text, ok = _year(year)
        official &= ok
        first = date(year, 1, 1)
        days_off += [first + timedelta(days=i) for i, c in enumerate(text) if c in "1"]
    days_off = [d for d in days_off if start - timedelta(days=10) <= d <= end]

    periods, run = [], []
    for d in days_off + [None]:
        if run and (d is None or (d - run[-1]).days != 1):
            names = [HOLIDAYS[(x.month, x.day)] for x in run if (x.month, x.day) in HOLIDAYS]
            if len(run) >= MIN_DAYS and names and run[-1] >= start:
                periods.append({"name": names[0], "first_day": run[0].isoformat(),
                                "last_day": run[-1].isoformat(), "days": len(run)})
            run = []
        if d is not None:
            run.append(d)
    return periods, official
