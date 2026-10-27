"""КОНТРАКТ между агентом и бэком
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class FlightOption(BaseModel):
    id: str
    source: Literal["aviasales", "yandex_rasp", "seed"]
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
    # коды: wifi, pool, parking, kids, pets, breakfast, beach_line, spa; None = «нет данных»
    amenities: dict[str, bool | None]
    description: str | None = None
    # плюсы и минусы расположения: факты из карты («пляж в 300 м», «до аэропорта 70 км»), не отзывы
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    photos: list[str] = Field(default_factory=list)
    website: str | None = None
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
    cart: list[CartItem] = Field(default_factory=list)
    cart_total_rub: int = 0
    issues: list[Issue] = Field(default_factory=list)
    orders: list[str] = Field(default_factory=list)


class Order(BaseModel):
    id: str
    session_id: str
    status: Literal["confirmed", "cancelled"]
    items: list[CartItem]
    total_rub: int
    payment: Literal["simulated"] = "simulated"
    created_at: datetime


class Block(BaseModel):
    type: Literal["flights", "hotels", "comparison", "itinerary", "cart", "order", "confirm",
                  "question", "error"]
    data: dict


class AgentResponse(BaseModel):
    session_id: str
    message: str
    blocks: list[Block] = Field(default_factory=list)
    state: TripState
    tool_calls: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str
    message: str
