"""Инструменты сборки поездки: требования, корзина, проверка и оформление заказа."""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.types import interrupt

from .. import memory, providers
from ..contracts import Requirements
from ..trip import apply, describe_cart, describe_issues, diff_requirements, make_cart_item, refresh
from ._state import BadInput, current_trip, emit, emit_cart, prepare_changes, result, session_id, user_id


@tool
def update_requirements(runtime: ToolRuntime, check_in: str | None = None, check_out: str | None = None,
                        adults: int | None = None, children_ages: list[int] | None = None,
                        budget_rub: int | None = None, district: str | None = None,
                        must_have: list[str] | None = None, near_sea: bool | None = None,
                        origin_code: str | None = None, note: str | None = None,
                        assumption: str | None = None):
    """Запомнить новые или изменённые условия поездки. Передавай только то, что изменилось.

    Условия для подбора передавай прямо в plan_trip или search_hotels: они сохранят их сами,
    отдельный вызов не нужен. Этот инструмент — только для того, что подбор не принимает
    (note, assumption, бюджет на всю поездку при подборе одного отеля), или когда подбирать
    пока рано (нет дат или гостей). Не вызывай в одном шаге с plan_trip.

    Args:
        check_in: дата заезда YYYY-MM-DD. Если передать только её, длительность сохранится.
        check_out: дата выезда YYYY-MM-DD.
        adults: число взрослых.
        children_ages: возраста детей, например [5, 8]; [] — детей нет.
        budget_rub: бюджет на всю поездку в рублях; 0 — снять ограничение.
        district: район локации (список — в системном промпте); «любой» — снять.
        must_have: полный новый список обязательных удобств из: wifi, pool, parking, kids, pets,
            breakfast, beach_line, spa; [] — снять все.
        near_sea: нужен ли отель до 500 м от моря.
        origin_code: IATA-код города вылета.
        note: пожелание, которое не выражается полями («потише», «вид на море»).
        assumption: допущение, которое ты сделал сам («считаю 5 ночей»).
    """
    trip = current_trip(runtime)
    try:
        changes, remark = prepare_changes(
            trip.requirements, check_in=check_in, check_out=check_out, adults=adults,
            children_ages=children_ages, budget_rub=budget_rub, district=district,
            must_have=must_have, near_sea=near_sea, origin_code=origin_code, note=note)
    except BadInput as exc:
        return result(runtime, "update_requirements", str(exc), [])

    ops = []
    if changes:
        ops.append({"op": "requirements", "changes": changes})
    if assumption:
        ops.append({"op": "assumptions", "items": [assumption.strip()]})
    if not ops:
        return result(runtime, "update_requirements", "Ничего не изменилось: эти условия уже сохранены.", [])

    new = Requirements.model_validate(trip.requirements.model_dump() | changes)
    memory.learn_requirements(runtime.store, user_id(runtime), new)
    lines = [remark.strip()] if remark else []
    if changes:
        # первое заполнение условий — не «изменение»: иначе модель начинает ответ с «Что изменилось»
        replaced = any(getattr(trip.requirements, field) not in (None, [], False) for field in changes)
        label = "Изменилось" if replaced else "Сохранено"
        lines.append(f"{label}: " + "; ".join(diff_requirements(trip.requirements, new)) + ".")
        if replaced:
            lines.append("Начни ответ с того, что изменилось, и повтори поиск, если он уже был.")
    if assumption:
        lines.append(f"Допущение записано: {assumption.strip()}.")

    after = refresh(trip)
    for op in ops:
        after = apply(after, op)
    after = refresh(after)
    if after.cart:
        emit_cart(runtime, after)
        if after.issues:
            lines.append(describe_issues(after.issues))
    return result(runtime, "update_requirements", "\n".join(lines), ops)


def _cart_result(runtime: ToolRuntime, name: str, op: dict, done: str):
    after = refresh(apply(current_trip(runtime), op))
    emit_cart(runtime, after)
    text = f"{done}\n{describe_cart(after)}\n{describe_issues(after.issues)}"
    return result(runtime, name, text, [op])


@tool
def add_to_cart(option_id: str, runtime: ToolRuntime):
    """Положить в корзину выбранный пользователем отель или перелёт из последней выдачи.

    Отель в корзине один: новый заменяет прежний. Перелётов по одному туда и обратно.
    После добавления поездка проверяется автоматически.

    Args:
        option_id: id варианта из последнего search_hotels или search_flights.
    """
    item = make_cart_item(current_trip(runtime), option_id)
    if isinstance(item, str):
        return result(runtime, "add_to_cart", item, [])
    return _cart_result(runtime, "add_to_cart", {"op": "cart_add", "item": item.model_dump(mode="json")},
                        f"Добавлено: {item.title}")


@tool
def remove_from_cart(option_id: str, runtime: ToolRuntime):
    """Убрать вариант из корзины.

    Args:
        option_id: id позиции в корзине.
    """
    trip = current_trip(runtime)
    if not any(c.option_id == option_id for c in trip.cart):
        return result(runtime, "remove_from_cart", f"Позиции {option_id} в корзине нет.\n{describe_cart(trip)}", [])
    return _cart_result(runtime, "remove_from_cart", {"op": "cart_remove", "option_id": option_id}, "Убрано.")


@tool
def validate_trip(runtime: ToolRuntime) -> str:
    """Проверить собранную поездку: даты, наличие и цену отеля, состыковку рейсов с заселением,
    трансфер, бюджет, условия для детей и животных."""
    trip = current_trip(runtime)
    emit_cart(runtime, trip)
    return f"{describe_cart(trip)}\n{describe_issues(trip.issues)}"


@tool
def checkout(runtime: ToolRuntime):
    """Оформить заказ на всё, что лежит в корзине. Оплата имитируется.

    Вызывай только когда пользователь сам попросил оформить или оплатить, и отдельно от других
    инструментов. Пользователь подтвердит оплату кнопкой в интерфейсе: не проси его писать «да».
    """
    trip = current_trip(runtime)
    if not trip.cart:
        return result(runtime, "checkout", "Корзина пуста: сначала добавь выбранный отель.", [])
    blockers = [i for i in trip.issues if i.severity == "blocker"]
    if blockers:
        emit_cart(runtime, trip)
        return result(runtime, "checkout",
                      "Оформить нельзя, сначала нужно исправить:\n" + describe_issues(blockers)
                      + "\nОбъясни пользователю и предложи, как исправить.", [])

    # Граф останавливается здесь, пока пользователь не нажмёт кнопку. После ответа инструмент
    # выполняется заново с начала, поэтому всё выше не должно иметь побочных эффектов.
    change = trip.order_change or {}
    decision = interrupt({
        "type": "checkout",
        "items": [c.model_dump(mode="json") for c in trip.cart],
        "total_rub": trip.cart_total_rub,
        "issues": [i.model_dump() for i in trip.issues],
        # перенос заказа: новый заменит этот; дополнение: трансфер к этому заказу
        "replaces": change.get("order_id") if change.get("mode") == "replace" else None,
        "extends": change.get("order_id") if change.get("mode") == "extend" else None,
    })
    if not decision.get("approved"):
        text = "Пользователь не подтвердил оплату, заказ не оформлен."
        if said := decision.get("message"):
            text += f" Вместо этого он написал: «{said}». Ответь на это сообщение."
        return result(runtime, "checkout", text, [])

    order = providers.orders().create(session_id(runtime), trip.version, trip.cart, trip.cart_total_rub)
    replaced = change.get("order_id") if change.get("mode") == "replace" else None
    if replaced:
        providers.orders().cancel(replaced)
    emit(runtime, "order", order=order.model_dump(mode="json"), cancelled=replaced)
    hotel = next((c.title for c in order.items if c.kind == "hotel"), "без отеля")
    memory.remember_order(runtime.store, user_id(runtime), order.id, f"{hotel}, итого {order.total_rub} ₽")
    text = f"Заказ {order.id} оформлен, оплата имитирована (демо, деньги не списаны). Итого {order.total_rub} ₽.\n"
    if replaced:
        text += f"Он заменил заказ {replaced}: тот отменён, возврат тоже демо.\n"
    elif change.get("mode") == "extend":
        text += f"Это дополнение к заказу {change['order_id']}.\n"
    text += ("Назови пользователю номер заказа, коротко что в него вошло, и что оплата демонстрационная. "
             "Не пиши, что оплата прошла.")
    return result(runtime, "checkout", text, [{"op": "order", "order_id": order.id, "cancelled": replaced}])


@tool
def remember(fact: str, runtime: ToolRuntime) -> str:
    """Запомнить устойчивое предпочтение пользователя для следующих поездок.

    Только когда пользователь сам говорит о привычке или просит запомнить: «я всегда беру
    с завтраком», «у нас аллергия на кошек», «запомни, что летаем из Казани». Не для условий
    этой поездки — их сохраняет update_requirements.

    Args:
        fact: короткая формулировка от третьего лица: «предпочитает отели с завтраком».
    """
    if not user_id(runtime):
        return "Память недоступна в этом режиме, просто учти это в текущей поездке."
    memory.remember_fact(runtime.store, user_id(runtime), fact.strip())
    return "Запомнил. Скажи пользователю, что учтёшь это и в следующих поездках."


TOOLS = [update_requirements, add_to_cart, remove_from_cart, validate_trip, checkout, remember]
