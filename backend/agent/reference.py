import json
from functools import lru_cache

import requests

from .config import HTTP_TIMEOUT, SEED_DIR


def _load(name: str) -> list[dict]:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    path = SEED_DIR / f"{name}_ru.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(f"https://api.travelpayouts.com/data/ru/{name}.json", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except requests.RequestException:
        return []


@lru_cache
def cities() -> list[dict]:
    return _load("cities")


@lru_cache
def airlines() -> dict[str, str]:
    return {a["code"]: a.get("name") or a["code"] for a in _load("airlines") if a.get("code")}


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
