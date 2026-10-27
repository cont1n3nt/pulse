import json
import logging

import requests

from .config import HTTP_TIMEOUT, SEED_DIR

log = logging.getLogger(__name__)


def _load(name: str) -> list[dict]:
    path = SEED_DIR / f"{name}_ru.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(f"https://api.travelpayouts.com/data/ru/{name}.json", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        # Не кэшируем пустой результат навсегда: при сбое сети следующий вызов попробует снова.
        log.warning("Не удалось загрузить справочник %s, используется запасной список", name)
        return []
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


_CACHE: dict[str, list[dict]] = {}


def _cached(name: str) -> list[dict]:
    if name not in _CACHE:
        data = _load(name)
        if not data:
            return data
        _CACHE[name] = data
    return _CACHE[name]


def cities() -> list[dict]:
    return _cached("cities")


def airlines() -> dict[str, str]:
    return {a["code"]: a.get("name") or a["code"] for a in _cached("airlines") if a.get("code")}


def _norm(text: str) -> str:
    return text.strip().lower().replace("ё", "е").replace(" ", "-")


FALLBACK = {
    "москва": ("MOW", "Москва", "RU"), "санкт-петербург": ("LED", "Санкт-Петербург", "RU"),
    "питер": ("LED", "Санкт-Петербург", "RU"), "сочи": ("AER", "Сочи", "RU"),
    "казань": ("KZN", "Казань", "RU"), "калининград": ("KGD", "Калининград", "RU"),
    "стамбул": ("IST", "Стамбул", "TR"), "анталья": ("AYT", "Анталья", "TR"),
    "дубай": ("DXB", "Дубай", "AE"), "нью-йорк": ("NYC", "Нью-Йорк", "US"),
}


def find_city(name: str, limit: int = 3) -> list[dict]:
    query = _norm(name)
    matches = []
    for c in cities():
        names = [c.get("name") or ""] + list((c.get("name_translations") or {}).values())
        normalized = [_norm(n) for n in names if n]
        if query in normalized:
            score = 0
        elif any(n.startswith(query) for n in normalized):
            score = 1
        else:
            continue
        if not c.get("has_flightable_airport", True):
            score += 2
        matches.append((score, c))
    if matches:
        matches.sort(key=lambda m: m[0])
        return [{"code": c["code"], "name": c.get("name"), "country": c.get("country_code")}
                for _, c in matches[:limit]]
    if (hit := FALLBACK.get(query.replace("-", " ")) or FALLBACK.get(query)):
        return [{"code": hit[0], "name": hit[1], "country": hit[2]}]
    return []


def city_name(code: str) -> str:
    """Название города по IATA-коду; сам код, если справочник недоступен."""
    for c in cities():
        if c.get("code") == code:
            return c.get("name") or code
    return next((hit[1] for hit in FALLBACK.values() if hit[0] == code), code)
