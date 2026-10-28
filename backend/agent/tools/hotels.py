from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from .. import providers
from ..contracts import AMENITY_NAMES, Requirements
from ..location import HERE
from ..trip import check_in_time, check_out_time, diff_hotels, diff_requirements
from ._state import BadInput, current_trip, emit, prepare_changes, result

MEALS = {"none": "без питания", "breakfast": "завтрак", "half_board": "полупансион",
         "all_inclusive": "всё включено"}
KINDS = {"hotel": "отель", "guest_house": "гостевой дом", "apartment": "апартаменты",
         "hostel": "хостел", "resort": "курорт"}
DEFAULT_ADULTS = 2


def _years(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} год"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} года"
    return f"{n} лет"


def _guests(adults: int, children_ages: list[int]) -> str:
    text = f"{adults} взрослого" if adults == 1 else f"{adults} взрослых"
    if len(children_ages) == 1:
        text += f" и ребёнка {_years(children_ages[0])}"
    elif children_ages:
        text += f" и {len(children_ages)} детей ({', '.join(map(str, children_ages))} лет)"
    return "для " + text


def _title(h) -> str:
    stars = f"{h.stars}★ " if h.stars else ""
    return f"{stars}{KINDS[h.kind]}"


@tool
def search_hotels(runtime: ToolRuntime, check_in: str | None = None, check_out: str | None = None,
                  adults: int | None = None, children_ages: list[int] | None = None,
                  district: str | None = None, max_price_per_night: int | None = None,
                  must_have: list[str] | None = None, near_sea: bool | None = None):
    """Найти отели локации на даты: цена за ночь, итог за поездку, удобства, что рядом.

    Не переданные параметры берутся из сохранённых требований поездки, переданные сохраняются в них.
    Поэтому после update_requirements достаточно вызвать search_hotels() без аргументов.

    Args:
        check_in: дата заезда YYYY-MM-DD.
        check_out: дата выезда YYYY-MM-DD.
        adults: число взрослых.
        children_ages: возраста детей, например [5].
        district: район локации (список — в системном промпте); «любой» снимает фильтр.
        max_price_per_night: максимум за ночь в рублях; если не задан, считается из бюджета поездки.
        must_have: обязательные удобства из: wifi, pool, parking, kids, pets, breakfast, beach_line, spa.
        near_sea: только отели до 500 м от моря.
    """
    trip = current_trip(runtime)
    try:
        changes, remark = prepare_changes(
            trip.requirements, check_in=check_in, check_out=check_out, adults=adults,
            children_ages=children_ages, district=district, must_have=must_have, near_sea=near_sea)
    except BadInput as exc:
        return result(runtime, "search_hotels", str(exc), [])

    req = Requirements.model_validate(trip.requirements.model_dump() | changes)
    if not (req.check_in and req.check_out):
        return result(runtime, "search_hotels",
                      "Даты поездки неизвестны. Уточни у пользователя даты или месяц и число ночей.", [])
    ops = [{"op": "requirements", "changes": changes}] if changes else []
    lines = []
    if changes:
        lines.append("Требования обновлены: " + "; ".join(diff_requirements(trip.requirements, req)) + ".")
    if req.adults is None:
        changes["adults"] = DEFAULT_ADULTS
        req.adults = DEFAULT_ADULTS
        ops = [{"op": "requirements", "changes": changes},
               {"op": "assumptions", "items": [f"считаю {DEFAULT_ADULTS} взрослых"]}]
        lines.append(f"Состав гостей не назван: считаю {DEFAULT_ADULTS} взрослых, скажи это как допущение.")

    nights = (req.check_out - req.check_in).days
    limit_note = ""
    if max_price_per_night is None and req.budget_rub:
        flights = sum(c.price_rub or 0 for c in trip.cart if c.kind == "flight")
        max_price_per_night = max((req.budget_rub - flights) // nights, 0)
        limit_note = (f"Лимит за ночь посчитан из бюджета {req.budget_rub} ₽"
                      f"{f' минус перелёты {flights} ₽' if flights else ''}: до {max_price_per_night} ₽.")
        lines.append(limit_note)

    required = list(req.must_have)
    if req.children_ages and "kids" not in required:
        required.append("kids")
    found = providers.hotels().search(
        req.check_in, req.check_out, req.district, max_price_per_night, required, req.near_sea,
        exclude_kinds=("hostel",) if req.children_ages else ())
    if not found:
        text = (remark + "\n".join(lines) + "\nНичего не найдено. Назови, какое условие мешает, и предложи "
                "ослабить фильтры: другой район, больший бюджет, меньше обязательных удобств или другие даты.")
        return result(runtime, "search_hotels", text.strip(), ops)

    changed = diff_hotels(trip.hotels, found)
    if changed:
        lines.append("По сравнению с прошлой выдачей: " + "; ".join(changed) + ".")
    for h in found:
        sea = f"до моря {h.distance_to_sea_m} м" if h.distance_to_sea_m is not None else ("в горах" if HERE.in_mountains(h.district) else "море далеко")
        cancel = "бесплатная отмена" if h.free_cancellation else "без бесплатной отмены"
        line = (f"[{h.id}] {h.name}, {_title(h)}, {h.district}, {sea}, "
                f"{h.price_per_night_rub} ₽/ночь, итого {h.total_price_rub} ₽ за {h.nights} н., "
                f"{MEALS[h.meal]}, {cancel}")
        unknown = [AMENITY_NAMES[c] for c in required if h.amenities.get(c) is None]
        if unknown:
            line += f". Не подтверждено: {', '.join(unknown)}"
        if h.pros:
            line += f". Рядом: {'; '.join(h.pros[:3])}"
        if h.cons:
            line += f". Учесть: {'; '.join(h.cons[:2])}"
        lines.append(line)

    header = f"Найдено {_guests(req.adults, req.children_ages)}, {req.check_in:%d.%m.%Y}–{req.check_out:%d.%m.%Y}:"
    items = [h.model_dump(mode="json") for h in found]
    emit(runtime, "hotels", items=items)
    ops.append({"op": "hotels", "items": items})
    text = (remark + header + "\n" + "\n".join(lines)
            + "\nОтели и расстояния — реальные данные OpenStreetMap; цена, питание и отмена — демо.")
    return result(runtime, "search_hotels", text, ops)


@tool
def get_hotel_details(hotel_id: str, runtime: ToolRuntime) -> str:
    """Подробно об отеле по id из результатов поиска: адрес, удобства, что рядом, сайт, цена на даты.

    Args:
        hotel_id: id отеля, например osm-w123456789.
    """
    h = providers.hotels().get(hotel_id)
    if not h:
        return f"Отель {hotel_id} не найден."
    amenities = ", ".join(
        f"{AMENITY_NAMES.get(code, code)}: {'есть' if v else 'нет' if v is False else 'нет данных'}"
        for code, v in h.amenities.items()
    )
    sea = f"{h.distance_to_sea_m} м" if h.distance_to_sea_m is not None else "нет данных"
    lines = [
        f"{h.name} ({_title(h)}), {h.district}. Адрес: {h.address or 'нет данных'}.",
        "Объект, адрес и расстояния — OpenStreetMap; цена, питание и отмена — демо.",
        f"До моря: {sea}. До аэропорта: {h.distance_to_airport_km} км.",
        f"Заселение с {check_in_time(h):%H:%M}, выезд до {check_out_time(h):%H:%M}"
        + (" (типовые условия)." if h.check_in_time is None else "."),
        f"Удобства: {amenities}.",
        f"Рядом (по карте): {'; '.join(h.pros) or 'нет данных'}.",
        f"Учесть: {'; '.join(h.cons) or 'ничего особенного'}.",
        f"Сайт отеля: {'есть' if h.website else 'нет данных'}. Отзывов и фото в данных нет.",
    ]
    req = current_trip(runtime).requirements
    if req.check_in and req.check_out:
        q = providers.hotels().quote(hotel_id, req.check_in, req.check_out)
        if q and q.available:
            lines.append(f"На {req.check_in:%d.%m}–{req.check_out:%d.%m}: {q.price_per_night_rub} ₽/ночь, "
                         f"итого {q.total_price_rub} ₽, {MEALS[q.meal]}, "
                         f"{'бесплатная отмена' if q.free_cancellation else 'без бесплатной отмены'}.")
        else:
            lines.append(f"На {req.check_in:%d.%m}–{req.check_out:%d.%m} мест нет.")
    return "\n".join(lines)


TOOLS = [search_hotels, get_hotel_details]
