"""Общее для инструментов, которые читают и меняют состояние поездки."""

from datetime import date

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..adapters import hotels as hotels_db
from ..contracts import Requirements, TripState
from ..trip import itinerary, refresh

MAX_NIGHTS = 30
CLEAR_WORDS = ("", "любой", "любое", "неважно", "не важно")


class BadInput(ValueError):
    """Ошибка в аргументах инструмента: текст уходит модели, чтобы она исправилась."""


def current_trip(runtime: ToolRuntime) -> TripState:
    return refresh(TripState.model_validate(runtime.state.get("trip") or {}))


def session_id(runtime: ToolRuntime) -> str:
    return runtime.config["configurable"]["thread_id"]


def user_id(runtime: ToolRuntime) -> str | None:
    """Владелец чата: ключ долговременной памяти. None в CLI и тестах — тогда память не пишется."""
    return runtime.config["configurable"].get("user_id")


def emit(runtime: ToolRuntime, block: str, **data) -> None:
    """Блок для фронта: уходит в SSE сразу, не дожидаясь ответа модели."""
    runtime.stream_writer({"type": "block", "block": block, **data})


def cart_block(trip: TripState) -> dict:
    """Корзина для фронта: позиции, итог, проблемы и таймлайн поездки."""
    change = trip.order_change or {}
    return {"items": [c.model_dump(mode="json") for c in trip.cart], "total_rub": trip.cart_total_rub,
            "issues": [i.model_dump() for i in trip.issues], "itinerary": itinerary(trip),
            "replaces": change.get("order_id") if change.get("mode") == "replace" else None,
            "extends": change.get("order_id") if change.get("mode") == "extend" else None}


def emit_cart(runtime: ToolRuntime, trip: TripState) -> None:
    emit(runtime, "cart", **cart_block(trip))


def result(runtime: ToolRuntime, name: str, text: str, ops: list[dict]) -> Command:
    """Ответ инструмента вместе с операциями над состоянием поездки."""
    update = {"messages": [ToolMessage(text, name=name, tool_call_id=runtime.tool_call_id)]}
    if ops:
        update["trip"] = ops
    return Command(update=update)


def _next_occurrence(day: date, today: date) -> date | None:
    """Ближайшая дата с тем же днём и месяцем, не раньше сегодня (в пределах двух лет)."""
    for year in range(today.year, today.year + 3):
        try:
            candidate = day.replace(year=year)
        except ValueError:  # 29 февраля в невисокосный год
            continue
        if candidate >= today:
            return candidate
    return None


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadInput(f"Неверный формат даты {label}, нужен YYYY-MM-DD.") from None


def prepare_changes(current: Requirements, *, check_in: str | None = None, check_out: str | None = None,
                    adults: int | None = None, children_ages: list[int] | None = None,
                    budget_rub: int | None = None, district: str | None = None,
                    must_have: list[str] | None = None, near_sea: bool | None = None,
                    origin_code: str | None = None, note: str | None = None) -> tuple[dict, str]:
    """Проверяет аргументы и возвращает (изменения требований, пояснение для модели).

    None в аргументе = «не менять». Пустой список удобств или район «любой» сбрасывают значение.
    """
    changes: dict = {}
    remark = ""

    if check_in is not None or check_out is not None:
        d_in = _parse_date(check_in, "заезда") if check_in else current.check_in
        d_out = _parse_date(check_out, "выезда") if check_out else current.check_out
        if d_in is None or d_out is None:
            raise BadInput("Нужны обе даты: заезда и выезда. Уточни у пользователя.")
        if check_in and not check_out and current.check_in and current.check_out:
            # сдвиг заезда без выезда сохраняет длительность («на два дня позже»)
            d_out = d_in + (current.check_out - current.check_in)
        if d_out <= d_in:
            raise BadInput("Дата выезда должна быть позже заезда. Уточни даты у пользователя.")
        today = date.today()
        if d_in < today:
            # «1–5 мая», когда этот май уже прошёл, означает следующий год, а не ошибку пользователя
            shifted = _next_occurrence(d_in, today)
            if shifted is None:
                raise BadInput("Дата заезда в прошлом. Уточни даты у пользователя.")
            d_in, d_out = shifted, shifted + (d_out - d_in)
            remark = (f"Указанные даты уже прошли, поэтому взяты ближайшие будущие: "
                      f"{d_in:%d.%m.%Y}–{d_out:%d.%m.%Y}. Скажи об этом пользователю как допущение.\n")
        if (d_out - d_in).days > MAX_NIGHTS:
            raise BadInput(f"Больше {MAX_NIGHTS} ночей искать нельзя. Предложи сократить срок.")
        changes["check_in"], changes["check_out"] = d_in.isoformat(), d_out.isoformat()

    if adults is not None:
        if not 1 <= adults <= 10:
            raise BadInput("Взрослых должно быть от 1 до 10.")
        changes["adults"] = adults
    if children_ages is not None:
        if any(not 0 <= age <= 17 for age in children_ages):
            raise BadInput("Возраст ребёнка должен быть от 0 до 17 лет.")
        changes["children_ages"] = children_ages
    if budget_rub is not None:
        if budget_rub < 0:
            raise BadInput("Бюджет не может быть отрицательным.")
        changes["budget_rub"] = budget_rub or None
    if district is not None:
        if district.strip().lower() in CLEAR_WORDS:
            changes["district"] = None
        else:
            canonical = next((d for d in hotels_db.DISTRICTS if d.lower() == district.strip().lower()), None)
            if canonical is None:
                raise BadInput(f"Неизвестный район «{district}». Доступны: {', '.join(hotels_db.DISTRICTS)}.")
            changes["district"] = canonical
    if must_have is not None:
        bad = [c for c in must_have if c not in hotels_db.AMENITY_CODES]
        if bad:
            raise BadInput(f"Неизвестные коды удобств: {', '.join(bad)}. "
                           f"Допустимы: {', '.join(hotels_db.AMENITY_CODES)}.")
        changes["must_have"] = list(dict.fromkeys(must_have))
    if near_sea is not None:
        changes["near_sea"] = near_sea
    if origin_code is not None:
        changes["origin_code"] = origin_code.strip().upper() or None
    if note:
        changes["notes"] = list(dict.fromkeys(current.notes + [note.strip()]))

    # оставляем только то, что действительно меняется
    old = current.model_dump(mode="json")
    changes = {k: v for k, v in changes.items() if old.get(k) != v}
    return changes, remark
