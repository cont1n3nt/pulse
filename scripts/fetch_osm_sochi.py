"""Выгрузка объектов размещения Большого Сочи из OpenStreetMap в seed агента.

Запуск (из корня проекта, нужен интернет):
    uv run python scripts/fetch_osm_sochi.py            # с кешем ответов Overpass
    uv run python scripts/fetch_osm_sochi.py --refresh  # заново запросить Overpass

Пишет backend/seed/sochi/hotels.json и meta.json. Приложение этот скрипт не импортирует и к OSM
в работе не обращается. Данные © участники OpenStreetMap, лицензия ODbL.

Что берётся из OSM как есть: название, тип, адрес, координаты, звёзды, сайт, Wi-Fi.
Что вычисляется из геометрии OSM: район, расстояние до моря, до аэропорта, первая линия,
бассейн рядом с объектом, ближайшие пляж, канатная дорога, станция и достопримечательности.
Остальные удобства (дети, животные, парковка, завтрак, спа) в OSM почти не размечены: «нет данных».
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from math import cos, hypot, radians
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "backend" / "seed" / "sochi"
CACHE_DIR = ROOT / "backend" / "data" / "osm_cache"  # сырые ответы, в git не попадают
OVERPASS = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "pulse-hackathon-seed/1.0 (one-off export)"}

# внутригородские районы Сочи и федеральная территория Сириус: relation id в OSM
DISTRICT_RELATIONS = {
    "Сириус": 11865346,  # первым: раньше территория входила в Адлерский район
    "Адлер": 5650614,
    "Хоста": 907728,
    "Центр": 1116490,
    "Лазаревское": 1116460,
}
# У Красной Поляны нет административной границы в OSM: горный кластер Адлерского района
# (Красная Поляна, Эсто-Садок, Роза Хутор, Горки Город) выделяем по координатам.
MOUNTAIN_CLUSTER = {"lat_min": 43.60, "lon_min": 40.00}
BBOX = (43.35, 39.05, 44.10, 40.45)  # юг, запад, север, восток — для моря и окрестных объектов
AIRPORT = (43.4499, 39.9566)  # AER

TOURISM = "hotel|guest_house|hostel|apartment|motel|chalet"
RESORT_WORDS = re.compile(r"санатор|пансионат|курорт|resort|дом отдыха|база отдыха|spa", re.I)
# известные сети: только для оценки класса в модели цен, звёздами не показываем
# порядок важен: «Park Inn by Radisson» — это класс 4, поэтому суббренды проверяются раньше
BRAND_CLASS = {
    4: re.compile(r"park inn|novotel|mercure|azimut|азимут|sheraton|courtyard|four points|best western|"
                  r"valset|holiday inn|hilton|alean|алеан|bridge|бридж|imeretinsky|имеретинский", re.I),
    5: re.compile(r"radisson|hyatt|swiss[oô]tel|rixos|marriott|ritz|pullman|mriya|мрия|rodina|родина", re.I),
}
APART_WORDS = re.compile(r"апарт|apart", re.I)
# названия, которые попадают в «достопримечательности», но рядом с отелем ничего не говорят
JUNK_SIGHT = re.compile(r"детск\w* сад|школ|касс|гараж|снт|товарищ|кладбищ|автостоянк|парковк", re.I)
FIRST_LINE_M = 150
POOL_RADIUS_M = 40
SEA_BEACH_M = 300  # пляж у моря, а не у реки или озера
NEAR_M = {"beach": 1500, "aerialway": 1000, "station": 1500, "sight": 1200}


# --- Overpass -------------------------------------------------------------------------------

def overpass(name: str, query: str, refresh: bool) -> list[dict]:
    cache = CACHE_DIR / f"{name}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))["elements"]
    for attempt in range(4):
        try:
            resp = requests.post(OVERPASS, data={"data": query}, headers=HEADERS, timeout=300)
            if resp.status_code in (429, 504):
                raise requests.HTTPError(f"Overpass занят: {resp.status_code}")
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            wait = 20 * (attempt + 1)
            print(f"  {name}: {exc}, повтор через {wait} с", file=sys.stderr)
            time.sleep(wait)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(resp.text, encoding="utf-8")
    time.sleep(3)  # вежливость к публичному серверу
    return resp.json()["elements"]


def bbox() -> str:
    return ",".join(map(str, BBOX))


def point(el: dict) -> tuple[float, float] | None:
    if "lat" in el:
        return el["lat"], el["lon"]
    if "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    return None


# --- геометрия: локальная проекция в метры и сетка для поиска ближайшего ---------------------

LAT0 = 43.6
KX = 111_320 * cos(radians(LAT0))  # метров в градусе долготы на широте Сочи
KY = 110_574  # метров в градусе широты


def xy(lat: float, lon: float) -> tuple[float, float]:
    return lon * KX, lat * KY


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    (ax, ay), (bx, by) = xy(*a), xy(*b)
    return hypot(ax - bx, ay - by)


def seg_dist(p, a, b) -> float:
    """Расстояние от точки до отрезка (всё в метрах)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return hypot(px - (ax + t * dx), py - (ay + t * dy))


class Grid:
    """Ячейки ~1 км: ближайший объект ищем только в соседних ячейках, а не перебором всего."""

    CELL = 1000.0

    def __init__(self):
        self.cells: dict[tuple[int, int], list] = defaultdict(list)

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return int(x // self.CELL), int(y // self.CELL)

    def add(self, x: float, y: float, item) -> None:
        self.cells[self._key(x, y)].append(item)

    def near(self, x: float, y: float, radius_m: float):
        r = int(radius_m // self.CELL) + 1
        cx, cy = self._key(x, y)
        for i in range(cx - r, cx + r + 1):
            for j in range(cy - r, cy + r + 1):
                yield from self.cells.get((i, j), ())


def coast_grid(ways: list[dict]) -> Grid:
    grid = Grid()
    for way in ways:
        pts = [xy(n["lat"], n["lon"]) for n in way.get("geometry", [])]
        for a, b in zip(pts, pts[1:]):
            grid.add((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a, b))
    return grid


def sea_distance(grid: Grid, lat: float, lon: float) -> int | None:
    p = xy(lat, lon)
    for radius in (2_000, 10_000, 60_000):
        best = min((seg_dist(p, a, b) for a, b in grid.near(*p, radius)), default=None)
        if best is not None and best <= radius:
            return round(best)
    return None


def points_grid(elements: list[dict]) -> Grid:
    grid = Grid()
    for el in elements:
        if (pt := point(el)) is not None:
            grid.add(*xy(*pt), (pt, el.get("tags", {})))
    return grid


def nearest(grid: Grid, lat: float, lon: float, radius_m: float, named: bool = False):
    p = (lat, lon)
    found = [(dist_m(p, pt), tags) for pt, tags in grid.near(*xy(lat, lon), radius_m)
             if not named or tags.get("name")]
    found = [f for f in found if f[0] <= radius_m]
    return sorted(found, key=lambda f: f[0])


# --- преобразование объекта -----------------------------------------------------------------

def kind_of(tags: dict) -> str:
    tourism = tags["tourism"]
    if tourism in ("guest_house", "chalet"):
        return "guest_house"
    if tourism in ("apartment", "hostel"):
        return tourism
    if APART_WORDS.search(tags.get("name", "")):
        return "apartment"
    return "resort" if RESORT_WORDS.search(tags.get("name", "")) else "hotel"


def stars_of(tags: dict) -> int | None:
    match = re.match(r"\s*([1-5])", tags.get("stars", ""))
    return int(match.group(1)) if match else None


def price_class(tags: dict, kind: str, stars: int | None) -> int:
    """Оценка класса 1–5 только для демо-модели цен. Звёзды из OSM, если они есть, важнее."""
    if stars:
        return stars
    name = " ".join(tags.get(k, "") for k in ("name", "brand", "operator", "name:en"))
    for cls, pattern in BRAND_CLASS.items():
        if pattern.search(name):
            return cls
    if kind == "hostel":
        return 1
    if kind == "guest_house":
        return 2
    if kind == "resort":
        return 4
    levels = re.match(r"\d+", tags.get("building:levels", ""))
    if kind == "hotel" and levels and int(levels.group()) >= 8:
        return 4
    return 3


def address_of(tags: dict) -> str | None:
    street, house = tags.get("addr:street"), tags.get("addr:housenumber")
    if not street:
        return None
    place = tags.get("addr:city") or tags.get("addr:suburb") or "Сочи"
    return f"{place}, {street}" + (f", {house}" if house else "")


def website_of(tags: dict) -> str | None:
    url = (tags.get("website") or tags.get("contact:website") or "").strip().split(";")[0]
    return url if re.match(r"https?://[\w.-]+\.\w+", url) and len(url) <= 200 else None


def wifi_of(tags: dict) -> bool | None:
    value = tags.get("internet_access")
    if value in ("wlan", "yes", "wifi", "terminal;wlan"):
        return True
    return False if value == "no" else None


def km(m: float) -> str:
    return f"{m / 1000:.1f} км".replace(".", ",") if m >= 1000 else f"{round(m / 10) * 10} м"


def describe(tags: dict, lat: float, lon: float, sea_m: int | None, airport_km: float,
             grids: dict, has_pool_nearby: bool, in_mountains: bool) -> tuple[list[str], list[str]]:
    """Плюсы и минусы расположения — факты из геометрии OSM, без оценок и отзывов."""
    pros, cons = [], []
    if sea_m is not None and sea_m <= FIRST_LINE_M:
        pros.append("первая линия у моря")
    beach = nearest(grids["beach"], lat, lon, NEAR_M["beach"])
    if beach:
        pros.append(f"пляж в {km(beach[0][0])}")
    lift = in_mountains and nearest(grids["aerialway"], lat, lon, NEAR_M["aerialway"], named=True)
    if lift:
        pros.append(f"канатная дорога «{lift[0][1]['name']}» в {km(lift[0][0])}")
    station = nearest(grids["station"], lat, lon, NEAR_M["station"], named=True)
    if station:
        pros.append(f"ж/д станция «{station[0][1]['name']}» в {km(station[0][0])}")
    seen = set()
    for distance, sight in nearest(grids["sight"], lat, lon, NEAR_M["sight"], named=True):
        name = sight["name"]
        if name in seen or name == tags.get("name") or JUNK_SIGHT.search(name):
            continue
        seen.add(name)
        pros.append(f"рядом: {name} ({km(distance)})")
        if len(seen) == 2:
            break
    if has_pool_nearby:
        pros.append("бассейн на территории (по карте)")
    if sea_m is None or sea_m > 1500:
        cons.append("море далеко" if sea_m is None else f"до моря {km(sea_m)}")
    if airport_km > 40:
        cons.append(f"до аэропорта {round(airport_km)} км")
    return pros[:5], cons


def district_of(name: str, lat: float, lon: float) -> str:
    if name == "Адлер" and lat >= MOUNTAIN_CLUSTER["lat_min"] and lon >= MOUNTAIN_CLUSTER["lon_min"]:
        return "Красная Поляна"
    return name


# --- сборка ---------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="не брать кеш, запросить Overpass заново")
    refresh = parser.parse_args().refresh
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows по умолчанию в cp1251
    sys.stderr.reconfigure(encoding="utf-8")

    print("Объекты размещения по районам…")
    raw: dict[tuple[str, int], tuple[str, dict]] = {}
    for district, rel in DISTRICT_RELATIONS.items():
        query = (f"[out:json][timeout:180];area(id:{3600000000 + rel})->.d;"
                 f'nwr["tourism"~"^({TOURISM})$"]["name"](area.d);out tags center;')
        elements = overpass(f"hotels_{rel}", query, refresh)
        added = 0
        for el in elements:
            key = (el["type"], el["id"])
            if key not in raw:  # объект на границе Сириуса и Адлера относим к Сириусу
                raw[key] = (district, el)
                added += 1
        print(f"  {district}: {len(elements)} (новых {added})")

    print("Море, пляжи, бассейны, достопримечательности…")
    coast = overpass("coastline", f'[out:json][timeout:180];way["natural"="coastline"]({bbox()});out geom;', refresh)
    grids = {
        "beach": overpass("beaches", f'[out:json][timeout:180];nwr["natural"="beach"]({bbox()});out center tags;', refresh),
        "aerialway": points_grid(overpass("aerialways", f'[out:json][timeout:180];nwr["aerialway"="station"]({bbox()});out center tags;', refresh)),
        "station": points_grid(overpass("stations", f'[out:json][timeout:180];nwr["railway"="station"]["train"!="no"]({bbox()});out center tags;', refresh)),
        "sight": points_grid(overpass("sights", (
            f"[out:json][timeout:180];("
            f'nwr["tourism"~"^(attraction|museum|theme_park|zoo|aquarium)$"]["name"]({bbox()});'
            f'nwr["leisure"~"^(park|water_park|stadium)$"]["name"]({bbox()});'
            f");out center tags;"), refresh)),
        "pool": points_grid(overpass("pools", f'[out:json][timeout:180];nwr["leisure"="swimming_pool"]({bbox()});out center tags;', refresh)),
    }
    sea = coast_grid(coast)
    grids["beach"] = points_grid([b for b in grids["beach"] if (pt := point(b)) is not None
                                  and (d := sea_distance(sea, *pt)) is not None and d <= SEA_BEACH_M])

    print("Сборка…")
    hotels: list[dict] = []
    by_name: dict[str, list[dict]] = defaultdict(list)
    for (el_type, el_id), (district, el) in sorted(raw.items(), key=lambda kv: (kv[0][0] != "relation", kv[0][0] != "way")):
        tags = el.get("tags", {})
        pt = point(el)
        name = " ".join(tags.get("name", "").split())
        if pt is None or not name:
            continue
        lat, lon = pt
        # один отель часто нарисован и точкой, и контуром здания: берём первый (контур важнее)
        norm = name.lower().replace("ё", "е")
        if any(dist_m((lat, lon), (h["lat"], h["lon"])) < 150 for h in by_name[norm]):
            continue

        kind = kind_of(tags)
        stars = stars_of(tags)
        sea_m = sea_distance(sea, lat, lon)
        airport_km = round(dist_m((lat, lon), AIRPORT) / 1000, 1)
        pool_tag = tags.get("swimming_pool") in ("yes", "outdoor", "indoor")
        pool_near = any(t.get("access") not in ("yes", "public") for _, t in nearest(grids["pool"], lat, lon, POOL_RADIUS_M))
        district_name = district_of(district, lat, lon)
        pros, cons = describe(tags, lat, lon, sea_m, airport_km, grids, pool_near and not pool_tag,
                              district_name == "Красная Поляна")
        hotel = {
            "id": f"osm-{el_type[0]}{el_id}",
            "source": "osm",
            "is_demo": True,  # цена, наличие, питание и отмена — демо-модель; остальное из OSM
            "name": name,
            "kind": kind,
            "stars": stars,
            "district": district_name,
            "address": address_of(tags),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "distance_to_sea_m": sea_m if sea_m is not None and sea_m <= 15_000 else None,
            "distance_to_airport_km": airport_km,
            "amenities": {
                "wifi": wifi_of(tags),
                "pool": True if pool_tag or pool_near else None,
                "parking": None,
                "kids": None,
                "pets": None,
                "breakfast": None,
                "beach_line": sea_m is not None and sea_m <= FIRST_LINE_M,
                "spa": None,
            },
            "description": None,
            "pros": pros,
            "cons": cons,
            "tips": [],
            "photos": [],
            "website": website_of(tags),
            "osm_url": f"https://www.openstreetmap.org/{el_type}/{el_id}",
            "price_class": price_class(tags, kind, stars),
        }
        hotels.append(hotel)
        by_name[norm].append(hotel)

    hotels.sort(key=lambda h: (h["district"], h["name"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "hotels.json").write_text(json.dumps(hotels, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = defaultdict(int)
    for h in hotels:
        counts[h["district"]] += 1
    meta = {
        "source": "OpenStreetMap, Overpass API",
        "license": "ODbL 1.0, © участники OpenStreetMap — https://www.openstreetmap.org/copyright",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hotels": len(hotels),
        "by_district": dict(sorted(counts.items())),
        "real_fields": ["name", "kind", "stars", "address", "lat", "lon", "website", "amenities.wifi"],
        "computed_fields": ["district", "distance_to_sea_m", "distance_to_airport_km", "amenities.beach_line",
                            "amenities.pool", "pros", "cons"],
        "demo_fields": ["price", "availability", "meal", "free_cancellation"],
        "mountain_cluster_rule": f"Адлер, широта ≥ {MOUNTAIN_CLUSTER['lat_min']}, долгота ≥ {MOUNTAIN_CLUSTER['lon_min']}",
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def share(pred) -> str:
        return f"{sum(1 for h in hotels if pred(h)) * 100 // len(hotels)}%"

    print(f"Готово: {len(hotels)} объектов → {OUT_DIR / 'hotels.json'}")
    print("  по районам:", meta["by_district"])
    print("  адрес", share(lambda h: h["address"]), "· сайт", share(lambda h: h["website"]),
          "· звёзды", share(lambda h: h["stars"]), "· Wi-Fi известен", share(lambda h: h["amenities"]["wifi"] is not None),
          "· бассейн", share(lambda h: h["amenities"]["pool"]), "· первая линия", share(lambda h: h["amenities"]["beach_line"]),
          "· есть плюсы", share(lambda h: h["pros"]))


if __name__ == "__main__":
    main()
