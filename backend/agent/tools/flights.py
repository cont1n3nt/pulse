from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from .. import providers
from ..location import HERE
from ..reference import find_city
from ._state import current_trip, emit, result

DESTINATION_CODES = (HERE.airport.code,)


@tool
def resolve_city(name: str) -> str:
    """Найти IATA-код города по названию.

    Args:
        name: название города, как его написал пользователь («Москва», «Нью-Йорк»).
    """
    found = find_city(name)
    if not found:
        return f"Город «{name}» не найден. Попроси пользователя уточнить название."
    return "; ".join(f'{c["name"]} ({c["country"]}) = {c["code"]}' for c in found)


@tool
def search_flights(origin: str, destination: str, departure: str, runtime: ToolRuntime, limit: int = 5):
    """Найти дешёвые перелёты в одну сторону с ценами Aviasales (цена за одного пассажира).

    Для поездки туда и обратно вызови дважды: туда (город → код аэропорта локации) и обратно; код локации — в системном промпте.
    Выдача обратного направления не стирает выдачу «туда».

    Args:
        origin: IATA-код города вылета (из resolve_city), например MOW.
        destination: IATA-код города прилёта.
        departure: месяц YYYY-MM или точная дата YYYY-MM-DD.
        limit: сколько вариантов вернуть (1–10).
    """
    # сбой сети пробрасываем: ToolRetryMiddleware повторит вызов, а потом сообщит модели
    flights = providers.flights().fetch_flights(origin, destination, departure, None, limit)

    if not flights:
        return result(runtime, "search_flights",
                      "Билетов в кэше Aviasales нет. Предложи соседний месяц или другие даты.", [])

    lines = [
        f"[{f.id}] {f.carrier_name} {f.number}: {f.from_airport}→{f.to_airport}, "
        f"вылет {f.depart_at:%d.%m %H:%M}, прилёт {f.arrive_at:%d.%m %H:%M}, "
        f"в пути {f.duration_min // 60}ч{f.duration_min % 60:02d}м, пересадок {f.transfers}, "
        f"{f'{f.price_rub} ₽' if f.price_rub is not None else 'цена неизвестна'}"
        for f in flights
    ]
    content = "Найдено (цена за одного, багаж не гарантирован):\n" + "\n".join(lines)
    items = [f.model_dump(mode="json") for f in flights]
    emit(runtime, "flights", items=items)

    ops = [{"op": "flights", "items": items}]
    home = destination if origin.upper() in DESTINATION_CODES else origin
    if home.upper() not in DESTINATION_CODES and current_trip(runtime).requirements.origin_code != home.upper():
        ops.append({"op": "requirements", "changes": {"origin_code": home.upper()}})
    return result(runtime, "search_flights", content, ops)


TOOLS = [resolve_city, search_flights]
