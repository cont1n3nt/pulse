from langchain_core.tools import tool

from ..adapters.aviasales import SourceUnavailable, fetch_flights
from ..reference import find_city


@tool
def resolve_city(name: str) -> str:
    """
    Args:
        name: название города, как его написал пользователь («Москва», «Нью-Йорк»).
    """
    found = find_city(name)
    if not found:
        return f"Город «{name}» не найден. Попроси пользователя уточнить название."
    return "; ".join(f'{c["name"]} ({c["country"]}) = {c["code"]}' for c in found)


@tool(response_format="content_and_artifact")
def search_flights(origin: str, destination: str, departure: str,
                   return_date: str | None = None, limit: int = 5):
    """
    Args:
        origin: IATA-код города вылета (из resolve_city), например MOW.
        destination: IATA-код города прилёта, например NYC.
        departure: месяц YYYY-MM или точная дата YYYY-MM-DD.
        return_date: дата/месяц обратного вылета, если нужен билет туда-обратно.
        limit: сколько вариантов вернуть (1–10).
    """
    try:
        flights = fetch_flights(origin, destination, departure, return_date, limit)
    except SourceUnavailable as exc:
        return f"Сервис цен Aviasales недоступен ({exc}). Скажи честно, цены не выдумывай.", []

    if not flights:
        return "Билетов в кэше Aviasales нет. Предложи соседний месяц или другие даты.", []

    lines = [
        f"[{f.id}] {f.carrier_name} {f.number}: {f.from_airport}→{f.to_airport}, "
        f"вылет {f.depart_at:%d.%m %H:%M}, в пути {f.duration_min // 60}ч{f.duration_min % 60:02d}м, "
        f"пересадок {f.transfers}, {f'{f.price_rub} ₽' if f.price_rub is not None else 'цена неизвестна'}"
        for f in flights
    ]
    content = "Найдено (багаж не гарантирован):\n" + "\n".join(lines)
    artifact = [f.model_dump(mode="json") for f in flights]
    return content, artifact


TOOLS = [resolve_city, search_flights]
