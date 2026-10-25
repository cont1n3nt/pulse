"""КОНТРАКТ между агентом и бэком
"""

from datetime import datetime
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
    source: Literal["osm", "seed"]
    name: str
    city_code: str
    stars: int | None = None
    price_per_night_rub: int | None = None
    meal: str | None = None
    free_cancellation: bool | None = None
    lat: float | None = None
    lon: float | None = None


class Requirements(BaseModel):
    origin_code: str | None = None
    destination_code: str | None = None
    depart: str | None = None
    return_date: str | None = None
    adults: int = 1
    children_ages: list[int] = Field(default_factory=list)
    budget_rub: int | None = None
    notes: list[str] = Field(default_factory=list)


class CartItem(BaseModel):
    kind: Literal["flight", "hotel", "transfer"]
    option_id: str
    title: str
    price_rub: int | None


class TripState(BaseModel):
    stage: Literal["clarify", "search", "compare", "assemble", "checkout", "done"] = "clarify"
    requirements: Requirements = Field(default_factory=Requirements)
    assumptions: list[str] = Field(default_factory=list)
    flights: list[FlightOption] = Field(default_factory=list)
    hotels: list[HotelOption] = Field(default_factory=list)
    cart: list[CartItem] = Field(default_factory=list)



class Block(BaseModel):
    type: Literal["flights", "hotels", "comparison", "itinerary", "cart", "question", "error"]
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
