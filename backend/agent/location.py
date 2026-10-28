"""Локация агента: всё, что зависит от места, — районы, аэропорт, вокзалы, часовой пояс, море,
сезонность цен, тарифы такси и описания районов для промпта.

Один запуск работает по одной локации: PULSE_LOCATION (по умолчанию sochi). Локация — папка
backend/seed/<id>/:
    location.json  пишется руками: этот файл описывает формат;
    hotels.json    отели из OpenStreetMap, выгружает scripts/fetch_osm.py <id>;
    meta.json      откуда данные и какие поля реальные, пишет тот же скрипт.
Код агента названий городов и районов не содержит: новый город — новая папка, без правок кода.
"""

import json
from datetime import time, timedelta, timezone
from functools import cached_property

from pydantic import BaseModel, Field

from .config import LOCATION, SEED_DIR


class Names(BaseModel):
    """Название локации в нужных падежах: промпт и ответы инструментов собираются из них."""
    title: str  # «Большой Сочи»
    city: str  # «Сочи»: подпись поездки в интерфейсе
    to: str  # «в Большой Сочи»: собрать поездку куда
    within: str  # «в Сочи»: «уже в Сочи», «отели в Сочи»
    across: str  # «по Большому Сочи»: «работаю только по …»
    of: str  # «Сочи»: «район …»


class Airport(BaseModel):
    code: str  # IATA-код для Aviasales: город прилёта
    name: str
    lat: float
    lon: float


class District(BaseModel):
    name: str
    lat: float  # точка погоды района
    lon: float
    about: str  # характер района для промпта: общие сведения, не цены
    price_factor: float = 1.0  # демо-модель цены: район дороже или дешевле среднего
    mountains: bool = False  # горы: другая сезонность, море не в счёт


class Sea(BaseModel):
    lat: float  # точка морского прогноза
    lon: float
    norm_by_month: dict[int, int]  # многолетняя норма температуры воды, °C


class Station(BaseModel):
    name: str
    lat: float
    lon: float
    minutes_to_hub: int  # сколько поезд идёт от станции до конечной (hub)


class Rail(BaseModel):
    """Поезда как альтернатива перелёту. Нет секции — нет поездов, только самолёт."""
    hub: str  # конечная станция, к ней считается время в пути
    via_hub: list[str] = Field(default_factory=list)  # районы, куда едут от hub, а не от ближайшей станции
    arrive_at: time  # демо: прибытие в hub «туда»
    depart_at: time  # демо: отправление из hub «обратно»
    stations: list[Station]
    hours_from: dict[str, float]  # IATA-код города отправления → часов в пути до hub


class Taxi(BaseModel):
    """Демо-тариф такси и поправки ко времени дороги по маршруту OSRM."""
    base_rub: int
    per_km_rub: int
    speed_kmh: int  # если маршрута нет: средняя скорость с пробками
    traffic_factor: float = 1.3  # OSRM считает без пробок


class Example(BaseModel):
    label: str  # кнопка-подсказка в пустом чате
    prompt: str


class Location(BaseModel):
    id: str
    names: Names
    utc_offset_hours: int
    timezone: str  # IANA, для Open-Meteo
    airport: Airport
    check_in: time = time(14, 0)
    check_out: time = time(12, 0)
    districts: list[District]
    default_district: str  # погода, если район не выбран
    # сезонность цен: коэффициент по месяцам для побережья и для гор
    seasons: dict[str, dict[int, float]]
    high_season: list[tuple[str, str]]  # периоды «ММ-ДД»–«ММ-ДД», когда свободных мест меньше
    sea: Sea | None = None
    rail: Rail | None = None
    taxi: Taxi
    examples: list[Example] = Field(default_factory=list)
    photo_stopwords: list[str] = Field(default_factory=list)  # латиница названий, не отличающая отель

    @cached_property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.utc_offset_hours))

    @cached_property
    def district_names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.districts)

    def district(self, name: str | None) -> District | None:
        return next((d for d in self.districts if d.name == name), None)

    def in_mountains(self, district: str | None) -> bool:
        found = self.district(district)
        return bool(found and found.mountains)

    def season(self, district: str | None, month: int) -> float:
        return self.seasons["mountain" if self.in_mountains(district) else "coast"][month]

    def is_high_season(self, day) -> bool:
        key = f"{day:%m-%d}"
        return any(start <= key <= end for start, end in self.high_season)

    def describe_districts(self) -> str:
        return "\n".join(f"{d.name} — {d.about}" for d in self.districts)


def load(location_id: str = LOCATION) -> Location:
    path = SEED_DIR / location_id / "location.json"
    if not path.exists():
        known = sorted(p.parent.name for p in SEED_DIR.glob("*/location.json"))
        raise RuntimeError(f"Локация «{location_id}» не найдена: нет {path}. Есть: {', '.join(known)}")
    return Location.model_validate(json.loads(path.read_text(encoding="utf-8")))


HERE = load()
