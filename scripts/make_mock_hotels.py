"""Одноразовый генератор моковых отелей Большого Сочи.

Запуск: uv run python scripts/make_mock_hotels.py
Пишет backend/seed/sochi/hotels.json. Приложение этот скрипт не импортирует.
"""

import json
import random
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "backend" / "seed" / "sochi" / "hotels.json"
AIRPORT = (43.450, 39.957)  # AER

# район: (slug, центр, сколько отелей)
DISTRICTS = {
    "Адлер": ("adler", (43.428, 39.923), 9),
    "Центр": ("tsentr", (43.585, 39.723), 8),
    "Сириус": ("sirius", (43.408, 39.957), 7),
    "Красная Поляна": ("krasnaya-polyana", (43.680, 40.205), 7),
    "Хоста": ("hosta", (43.515, 39.866), 5),
    "Лазаревское": ("lazarevskoe", (43.909, 39.333), 4),
}

KIND_PREFIX = {
    "hotel": "Отель",
    "guest_house": "Гостевой дом",
    "apartment": "Апарт-отель",
    "hostel": "Хостел",
    "resort": "Курорт",
}
KIND_WEIGHTS = {"hotel": 40, "guest_house": 25, "apartment": 15, "hostel": 8, "resort": 12}
KIND_STARS = {
    "hotel": [3, 3, 4, 4, 5, None],
    "guest_house": [None, None, 2, 3],
    "apartment": [None, 3, 4],
    "hostel": [None, None, 2],
    "resort": [4, 5, 5],
}
KIND_PRICE = {  # диапазон «от» в рублях за ночь
    "hotel": (4000, 14000),
    "guest_house": (2000, 6000),
    "apartment": (3500, 11000),
    "hostel": (1500, 2800),
    "resort": (9000, 25000),
}

NAMES = [
    "Тихий Прибой", "Кипарисовая Бухта", "Седая Чайка", "Янтарный Бриз", "Горная Лилия",
    "Южный Ветер", "Мятная Волна", "Серебряный Платан", "Золотой Маяк", "Тёплый Камень",
    "Лавровая Роща", "Утренний Бриз", "Белый Парус", "Эвкалипт", "Малахитовая Бухта",
    "Каменный Причал", "Дикая Мимоза", "Облачный Перевал", "Ласковый Берег", "Морская Соль",
    "Ореховая Тропа", "Звонкий Ручей", "Пальмовый Дворик", "Алая Магнолия", "Тихая Гавань",
    "Сосновый Склон", "Лунная Дорожка", "Шёпот Волны", "Рассветный Мыс", "Кедровый Дом",
    "Прохладный Грот", "Синий Якорь", "Оливковая Ветвь", "Вечерний Бриз", "Горный Хрусталь",
    "Лазурная Тропинка", "Тёплая Галька", "Солнечный Двор", "Хрустальный Ключ", "Мирный Берег",
    "Бирюзовая Заводь", "Дальний Маяк", "Тенистый Сад", "Перламутровая Ракушка", "Ветреный Мыс",
]

AMENITIES = ["wifi", "pool", "parking", "kids", "pets", "breakfast", "beach_line", "spa"]

PROS = {
    "any": ["чисто и уютно", "приветливый персонал", "хорошее соотношение цены и качества",
            "быстрый wi-fi", "удобная кровать", "тихий двор"],
    "sea": ["до моря рукой подать", "пляж в пешей доступности", "красивый вид на море"],
    "breakfast": ["вкусные завтраки", "разнообразный завтрак"],
    "mountain": ["свежий горный воздух", "рядом подъёмники", "красивые виды на горы"],
    "center": ["рядом набережная и кафе", "удобно добираться пешком до достопримечательностей"],
    "airport": ["близко аэропорт", "рядом Олимпийский парк"],
}
CONS = {
    "any": ["тонкие стены", "небольшие номера", "слабое кондиционирование", "бывает шумно по вечерам",
            "ремонт давно не обновляли"],
    "far": ["до моря далеко", "дорога к морю в горку", "к пляжу нужен транспорт"],
    "beach": ["пляж галечный", "на пляже многолюдно в сезон"],
    "breakfast": ["скромный завтрак", "завтрак по расписанию, очередь"],
    "parking": ["сложно с парковкой", "мало мест на парковке"],
    "center": ["шумно из-за центральных улиц"],
    "airport": ["слышен шум самолётов"],
}
TIPS = {
    "any": ["бронируйте заранее на высокий сезон", "уточняйте время заселения заранее",
            "берите номер на верхних этажах"],
    "sea": ["лучше ходить на пляж утром, пока свободно"],
    "far": ["удобнее приезжать на машине или заказывать такси"],
    "mountain": ["берите тёплые вещи, вечерами прохладно"],
    "airport": ["просите номер с окнами во двор"],
}


def haversine_km(lat1, lon1, lat2, lon2):
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def pick(rng, groups, tags, k):
    pool = []
    for tag in ("any", *tags):
        pool += groups.get(tag, [])
    return rng.sample(pool, min(k, len(pool)))


def make_hotel(rng, index, district, slug, center, kind, name):
    lat = round(center[0] + rng.uniform(-0.01, 0.01), 5)
    lon = round(center[1] + rng.uniform(-0.01, 0.01), 5)
    mountain = district == "Красная Поляна"
    sea_m = None if mountain else rng.choice([rng.randint(50, 400), rng.randint(200, 1200), rng.randint(800, 3000)])
    stars = rng.choice(KIND_STARS[kind])
    lo, hi = KIND_PRICE[kind]
    price = round(rng.uniform(lo, hi) / 100) * 100
    if stars == 5:
        price = max(price, 12000)

    amenities = {
        "wifi": rng.random() < 0.95,
        "pool": kind in ("hotel", "resort", "apartment") and rng.random() < 0.5,
        "parking": rng.random() < 0.6,
        "kids": rng.random() < 0.7,
        "pets": rng.random() < 0.3,
        "breakfast": kind != "hostel" and rng.random() < 0.6,
        "beach_line": sea_m is not None and sea_m <= 150 and rng.random() < 0.7,
        "spa": kind in ("hotel", "resort") and (stars or 0) >= 4 and rng.random() < 0.7,
    }
    for code in rng.sample(AMENITIES, rng.choice([2, 3])):
        amenities[code] = None

    tags = []
    if sea_m is not None and sea_m <= 400:
        tags.append("sea")
    if sea_m is not None and sea_m > 1200:
        tags.append("far")
    if sea_m is not None and sea_m > 400:
        tags.append("beach")
    if mountain:
        tags.append("mountain")
    if district == "Центр":
        tags.append("center")
    if district in ("Адлер", "Сириус"):
        tags.append("airport")
    if amenities["breakfast"]:
        tags.append("breakfast")
    if amenities["parking"] is False:
        tags.append("parking")

    dist_airport = round(haversine_km(lat, lon, *AIRPORT), 1)
    return {
        "id": f"mock-{slug}-{index:03d}",
        "source": "mock",
        "is_demo": True,
        "name": f"{KIND_PREFIX[kind]} «{name}»",
        "kind": kind,
        "stars": stars,
        "district": district,
        "address": None,
        "lat": lat,
        "lon": lon,
        "distance_to_sea_m": sea_m,
        "distance_to_airport_km": dist_airport,
        "amenities": amenities,
        "description": f"Демо-объект в районе «{district}». Данные вымышленные.",
        "pros": pick(rng, PROS, tags, rng.randint(1, 3)),
        "cons": pick(rng, CONS, tags, rng.randint(1, 3)),
        "tips": pick(rng, TIPS, tags, rng.randint(1, 2)),
        "photos": [],
        "price_from_rub": price,
    }


def main():
    rng = random.Random(42)
    names = rng.sample(NAMES, 40)
    kinds = list(KIND_WEIGHTS)
    weights = list(KIND_WEIGHTS.values())
    hotels, n = [], 0
    for district, (slug, center, count) in DISTRICTS.items():
        for _ in range(count):
            kind = rng.choices(kinds, weights)[0]
            hotels.append(make_hotel(rng, n + 1, district, slug, center, kind, names[n]))
            n += 1
    for hotel in hotels:
        hotel["address"] = f"ул. Демонстрационная, {rng.randint(1, 120)}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(hotels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Записано {len(hotels)} отелей в {OUT}")


if __name__ == "__main__":
    main()
