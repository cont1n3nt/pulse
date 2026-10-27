"""Инструменты полной поездки: пакеты «перелёт + трансфер + отель», выбор пакета, праздники."""

import logging
from datetime import date

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from .. import memory, packages
from ..adapters import aviasales, holidays, weather
from ..adapters import hotels as hotels_db
from ..contracts import Requirements
from ..reference import city_name, find_city
from ..trip import _rub, apply, describe_cart, describe_issues, refresh
from ._state import BadInput, current_trip, emit, emit_cart, prepare_changes, result, user_id

log = logging.getLogger(__name__)

SOCHI = "AER"
DEFAULT_ORIGIN = ("MOW", "Москва")
DEFAULT_ADULTS = 2
CANDIDATES = 600  # с запасом: Эконом и Комфорт ищутся по всей выдаче, а не около ориентира цены
TRANSFER_ESTIMATE_RUB = 3000  # для лимита за ночь, пока трансфер конкретного отеля неизвестен


def _origin(runtime: ToolRuntime, given: str | None, req: Requirements) -> tuple[str | None, str | None, str]:
    """(IATA, допущение, ошибка) для города вылета: аргумент, требования, память, Москва."""
    if given:
        text = given.strip()
        if len(text) == 3 and text.isascii() and text.isalpha():
            return text.upper(), None, ""
        found = find_city(text)
        if not found:
            return None, None, f"Город вылета «{given}» не найден. Уточни у пользователя."
        return found[0]["code"], None, ""
    if req.origin_code:
        return req.origin_code, None, ""
    remembered = memory.load(runtime.store, user_id(runtime)).get("origin_code")
    if remembered:
        return remembered, f"вылет из города {city_name(remembered)}, как в прошлых поездках", ""
    return DEFAULT_ORIGIN[0], f"вылет из города {DEFAULT_ORIGIN[1]}", ""


@tool
def plan_trip(runtime: ToolRuntime, origin: str | None = None, check_in: str | None = None,
              check_out: str | None = None, adults: int | None = None, children_ages: list[int] | None = None,
              budget_rub: int | None = None, district: str | None = None, near_sea: bool | None = None,
              assumption: str | None = None):
    """Собрать поездку целиком: 2–3 готовых пакета (перелёт туда и обратно + трансфер + отель)
    с одной итоговой суммой, сравнением в рублях, проверкой состыковок и погодой.

    Основной инструмент подбора. Новые или изменённые условия передавай прямо сюда одним вызовом:
    они сохранятся, а не названные возьмутся из сохранённых. Не вызывай его в одном шаге
    с update_requirements. Если пользователь едет без перелёта (на машине, уже в Сочи) —
    вместо него search_hotels.

    Args:
        origin: город вылета (название или IATA-код). Не передавай, если он уже сохранён:
            тогда возьмётся сохранённый, из прошлых поездок или Москва как допущение.
        check_in: дата заезда YYYY-MM-DD. Если передать только её, длительность сохранится.
        check_out: дата выезда YYYY-MM-DD.
        adults: число взрослых.
        children_ages: возраста детей, например [6]; [] — детей нет.
        budget_rub: бюджет на всю поездку вместе с перелётами, в рублях.
        district: район: Адлер, Центр, Сириус, Красная Поляна, Хоста, Лазаревское; «любой» — снять.
        near_sea: нужен ли отель до 500 м от моря.
        assumption: допущение, которое ты сделал сам («считаю 7 ночей»).
    """
    trip = current_trip(runtime)
    try:
        given, remark = prepare_changes(
            trip.requirements, check_in=check_in, check_out=check_out, adults=adults,
            children_ages=children_ages, budget_rub=budget_rub, district=district, near_sea=near_sea)
    except BadInput as exc:
        return result(runtime, "plan_trip", str(exc), [])
    req = Requirements.model_validate(trip.requirements.model_dump() | given)
    if not (req.check_in and req.check_out):
        return result(runtime, "plan_trip",
                      "Даты поездки неизвестны. Уточни у пользователя даты или месяц и число ночей.", [])
    code, origin_assumption, error = _origin(runtime, origin, req)
    if error:
        return result(runtime, "plan_trip", error, [])

    changes = dict(given)
    assumptions = [assumption.strip()] if assumption else []
    notes = [remark.strip()] if remark else []
    if code != req.origin_code:
        changes["origin_code"] = code
    if req.adults is None:
        changes["adults"] = DEFAULT_ADULTS
        assumptions.append(f"считаю {DEFAULT_ADULTS} взрослых")
    if origin_assumption:
        assumptions.append(origin_assumption)
    req = Requirements.model_validate(req.model_dump() | changes)
    if not origin_assumption:
        memory.learn_requirements(runtime.store, user_id(runtime), req)  # город вылета назвал сам пользователь
    nights = (req.check_out - req.check_in).days
    guests = max(req.guests, 1)

    outbound = back = None
    if code != SOCHI:
        try:
            outbound = aviasales.cheapest_on(code, SOCHI, req.check_in)
            back = aviasales.cheapest_on(SOCHI, code, req.check_out)
        except aviasales.SourceUnavailable:
            notes.append("Сервис цен на перелёты недоступен: пакеты собраны без перелётов, скажи об этом.")
        else:
            if outbound is None:
                notes.append(f"На {req.check_in:%d.%m} в кэше Aviasales нет билета туда: пакет без него.")
            if back is None:
                notes.append(f"На {req.check_out:%d.%m} в кэше Aviasales нет билета обратно: пакет без него.")

    flights_rub = packages.flights_cost(outbound, back, guests) or 0
    limit = None
    if req.budget_rub:
        limit = max((req.budget_rub - flights_rub - TRANSFER_ESTIMATE_RUB) // nights, 0)
    required = list(req.must_have) + (["kids"] if req.children_ages and "kids" not in req.must_have else [])
    candidates = hotels_db.search(
        req.check_in, req.check_out, req.district, None, required, req.near_sea,
        exclude_kinds=("hostel",) if req.children_ages else (), limit=CANDIDATES, diversify=False,
        target_per_night=limit)
    ops = []
    if changes:
        ops.append({"op": "requirements", "changes": changes})
    if assumptions:
        ops.append({"op": "assumptions", "items": assumptions})
    if not candidates:
        text = ("\n".join(notes) + "\nОтелей под условия не нашлось. Назови, какое условие мешает, и предложи "
                "ослабить: другой район, больше бюджет, другие даты.").strip()
        return result(runtime, "plan_trip", text, ops)

    built = packages.build(req, candidates, outbound, back)
    optimum = next(p for p in built if p.id == "optimum")
    flex = None
    if outbound and back:
        try:
            flex = packages.flex_dates(
                req, optimum.hotel,
                aviasales.prices_around(code, SOCHI, req.check_in, packages.FLEX_DAYS),
                aviasales.prices_around(SOCHI, code, req.check_out, packages.FLEX_DAYS))
        except aviasales.SourceUnavailable:
            flex = None
    forecast = weather.trip_weather(optimum.hotel.district, req.check_in, req.check_out)

    shown = hotels_db.diverse(candidates, 5)
    items = [p.model_dump(mode="json") for p in built]
    emit(runtime, "packages", items=items, flex=flex, weather=forecast)
    emit(runtime, "hotels", items=[h.model_dump(mode="json") for h in shown])
    flights = [f.model_dump(mode="json") for f in (outbound, back) if f]
    if flights:
        emit(runtime, "flights", items=flights)
        ops.append({"op": "flights", "items": flights})
    ops += [{"op": "hotels", "items": [h.model_dump(mode="json") for h in shown]},
            {"op": "packages", "items": items}, {"op": "weather", "data": forecast}]

    lines = [f"Пакеты на {req.check_in:%d.%m}–{req.check_out:%d.%m} ({nights} н.), гостей {guests}, вылет из {code}:"]
    lines.append(packages.describe(built))
    if req.budget_rub:
        lines.append(f"Бюджет {_rub(req.budget_rub)}.")
    if flex:
        lines.append(f"Гибкие даты: если сдвинуть поездку на {flex['shift_days']:+d} дн. "
                     f"({date.fromisoformat(flex['check_in']):%d.%m}–{date.fromisoformat(flex['check_out']):%d.%m}), "
                     f"Оптимум выйдет дешевле на {_rub(flex['saving_rub'])}. Предложи это одной фразой.")
    lines.append(weather.describe(forecast))
    lines += notes
    if assumptions:
        lines.append("Допущения, назови их: " + "; ".join(assumptions) + ".")
    lines.append("Перелёты — реальные недавние цены из кэша Aviasales (за одного, багаж не гарантирован); "
                 "отели — OpenStreetMap; цены отелей и такси — демо. Рекомендуй один пакет и объясни почему.")
    lines.append("Ответ — до 100 слов: что собрал, что советуешь и почему, одна главная разница, гибкие даты "
                 "или погода одной фразой, вопрос. Подробности пользователь видит в карточках.")
    return result(runtime, "plan_trip", "\n".join(lines), ops)


@tool
def choose_package(package_id: str, runtime: ToolRuntime):
    """Положить в корзину весь пакет: отель, перелёты туда и обратно и трансфер.

    Вызывай, когда пользователь выбрал пакет («беру Оптимум», «давай первый пакет»).

    Args:
        package_id: economy, optimum или comfort из последнего plan_trip.
    """
    trip = current_trip(runtime)
    pkg = next((p for p in trip.packages if p.id == package_id), None)
    if pkg is None:
        known = ", ".join(p.id for p in trip.packages) or "нет"
        return result(runtime, "choose_package",
                      f"Пакета {package_id} нет в последней выдаче (есть: {known}). Если условия менялись, "
                      "сначала заново вызови plan_trip.", [])
    ops = [{"op": "cart_add", "item": item.model_dump(mode="json")} for item in packages.items(pkg)]
    after = trip
    for op in ops:
        after = apply(after, op)
    after = refresh(after)
    emit_cart(runtime, after)
    text = f"В корзине пакет «{pkg.label}».\n{describe_cart(after)}\n{describe_issues(after.issues)}"
    return result(runtime, "choose_package", text, ops)


@tool
def holiday_dates() -> str:
    """Длинные выходные и праздники в России на год вперёд: чтобы понять «на майские»,
    «на новогодние», «на ноябрьские» и предложить точные даты."""
    periods, official = holidays.long_weekends(date.today())
    if not periods:
        return "Длинных выходных в ближайший год не найдено."
    lines = [f"{p['name']}: выходные {date.fromisoformat(p['first_day']):%d.%m.%Y}–"
             f"{date.fromisoformat(p['last_day']):%d.%m.%Y} ({p['days']} дн.)" for p in periods]
    source = ("производственный календарь isdayoff.ru, с переносами" if official
              else "без учёта переносов: календарь недоступен, предупреди об этом")
    return ("Длинные выходные (" + source + "):\n" + "\n".join(lines)
            + "\nЗаезд — в первый выходной, выезд — в последний; можно вылететь вечером накануне.")


TOOLS = [plan_trip, choose_package, holiday_dates]
