"""Контракт между агентом и бэком: варианты поездки, состояние, заказ, ответ агента."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class FlightOption(BaseModel):
    """Переезд в одну сторону: самолёт или поезд (тогда from_airport/to_airport — город и станция)."""
    id: str
    source: Literal["aviasales", "yandex_rasp", "rail_demo", "seed"]
    mode: Literal["plane", "train"] = "plane"
    carrier_code: str
    carrier_name: str
    number: str
    from_airport: str
    to_airport: str
    depart_at: datetime
    arrive_at: datetime
    duration_min: int
    transfers: int
    price_rub: int | None = None
    price_is_demo: bool = False  # поезд из Яндекс Расписаний без цены в API: цена купе — демо
    title: str | None = None  # маршрут поезда, например «Москва — Адлер»
    baggage_included: bool | None = None
    link: str | None = None


class HotelOption(BaseModel):
    id: str
    source: Literal["mock", "osm", "yandex", "seed"]
    # True: цена, наличие, питание и отмена — демо-модель (объект и факты о нём при этом реальные)
    is_demo: bool = True
    name: str
    kind: Literal["hotel", "guest_house", "apartment", "hostel", "resort"]
    stars: int | None = None
    district: str
    address: str | None = None
    lat: float
    lon: float
    distance_to_sea_m: int | None = None
    distance_to_airport_km: float | None = None
    # дорога из аэропорта по графу дорог OSM (OSRM), без пробок
    airport_drive_min: int | None = None
    airport_drive_km: float | None = None
    # коды: wifi, pool, parking, kids, pets, breakfast, beach_line, spa; None = «нет данных»
    amenities: dict[str, bool | None]
    description: str | None = None
    # плюсы и минусы расположения: факты из карты («пляж в 300 м», «до аэропорта 70 км»), не отзывы
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    website: str | None = None
    phone: str | None = None
    osm_url: str | None = None  # объект на openstreetmap.org: любой факт можно проверить
    # результат поиска (заполняет адаптер)
    check_in: date | None = None
    check_out: date | None = None
    nights: int | None = None
    price_per_night_rub: int | None = None
    total_price_rub: int | None = None
    available: bool | None = None
    meal: Literal["none", "breakfast", "half_board", "all_inclusive"] | None = None
    free_cancellation: bool | None = None


class TransferOption(BaseModel):
    """Такси аэропорт (или вокзал) ⇄ отель, туда и обратно. Время — по дорогам OSM, цена — демо-тариф."""
    hotel_id: str
    start: str = "аэропорт"  # откуда такси: «аэропорт» или «вокзал Сочи»
    minutes: int  # в одну сторону, с выходом из аэропорта и запасом на пробки
    km: float | None = None
    trips: int = 2
    price_rub: int  # за все поездки, на машину
    car: Literal["standard", "minivan"] = "standard"
    is_demo: bool = True


class Package(BaseModel):
    """Готовый вариант поездки: перелёты туда и обратно, трансфер и отель с одной итоговой суммой."""
    id: Literal["economy", "optimum", "comfort"]
    label: str
    hotel: HotelOption
    outbound: FlightOption | None = None
    back: FlightOption | None = None
    transfer: TransferOption | None = None
    flights_rub: int | None = None  # за всех пассажиров, туда и обратно
    total_rub: int
    per_person_rub: int
    within_budget: bool | None = None
    highlights: list[str] = Field(default_factory=list)  # чем хорош, факты
    tradeoffs: list[str] = Field(default_factory=list)  # чем отличается от соседнего варианта, в рублях
    issues: list["Issue"] = Field(default_factory=list)


class Requirements(BaseModel):
    """Что известно о поездке. None = пользователь ещё не сказал."""
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    budget_rub: int | None = None  # на всю поездку
    district: str | None = None
    must_have: list[str] = Field(default_factory=list)
    near_sea: bool = False
    origin_code: str | None = None
    transport: Literal["plane", "train"] | None = None  # None — самолёт, если пользователь не просил поезд
    notes: list[str] = Field(default_factory=list)

    @property
    def guests(self) -> int:
        return (self.adults or 0) + len(self.children_ages)


class CartItem(BaseModel):
    kind: Literal["flight", "hotel", "transfer"]
    option_id: str
    title: str
    price_rub: int | None  # за всех гостей
    leg: Literal["outbound", "return"] | None = None  # только для перелётов
    # копия варианта на момент добавления: корзина не зависит от следующей выдачи
    snapshot: dict = Field(default_factory=dict)


class Issue(BaseModel):
    """Проблема сборки поездки. blocker не даёт оформить заказ."""
    code: str
    severity: Literal["blocker", "warning", "info"]
    message: str


class TripState(BaseModel):
    stage: Literal["clarify", "search", "compare", "assemble", "checkout", "done"] = "clarify"
    version: int = 0
    requirements: Requirements = Field(default_factory=Requirements)
    assumptions: list[str] = Field(default_factory=list)
    flights: list[FlightOption] = Field(default_factory=list)
    hotels: list[HotelOption] = Field(default_factory=list)
    packages: list[Package] = Field(default_factory=list)
    weather: dict | None = None  # погода на даты поездки: adapters/weather.trip_weather
    cart: list[CartItem] = Field(default_factory=list)
    cart_total_rub: int = 0
    issues: list[Issue] = Field(default_factory=list)
    orders: list[str] = Field(default_factory=list)


class AgentReply(BaseModel):
    """Ответ пользователю. Вызывай один раз, в самом конце хода, когда все нужные инструменты уже отработали."""
    text: str = Field(description="Текст ответа: обычные предложения без разметки, до 100 слов")
    recommendation: str | None = Field(
        default=None,
        description="id пакета (economy, optimum, comfort) или отеля из последней выдачи, который советуешь; "
                    "null, если советовать нечего")
    suggestions: list[str] = Field(
        default_factory=list,
        description="2–3 коротких следующих реплики от лица пользователя для кнопок, до 30 символов: "
                    "«Беру Оптимум», «А на день раньше?», «Покажи дешевле»")


class Order(BaseModel):
    id: str
    session_id: str
    status: Literal["confirmed", "cancelled"]
    items: list[CartItem]
    total_rub: int
    payment: Literal["simulated"] = "simulated"
    created_at: datetime


class Block(BaseModel):
    type: Literal["flights", "hotels", "packages", "cart", "order", "confirm", "reply", "error"]
    data: dict


class AgentResponse(BaseModel):
    session_id: str
    message: str
    blocks: list[Block] = Field(default_factory=list)
    state: TripState
    tool_calls: list[str] = Field(default_factory=list)


Package.model_rebuild()  # ссылается на Issue, объявленный ниже
