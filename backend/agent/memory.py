"""Долговременная память о пользователе: между чатами, в хранилище LangGraph (Store).

Память поездки (корзина, требования) живёт в чекпоинтере и привязана к чату. Здесь — то, что
полезно в следующих чатах: город вылета, состав семьи, предпочтения, прошлые заказы.
Записывает код (из сохранённых требований и заказов) и инструмент remember (явные просьбы
пользователя), поэтому в память не попадают догадки модели. Пользователь видит и стирает
её в профиле.
"""

from datetime import date

from langgraph.store.base import BaseStore

from .contracts import Requirements
from .reference import city_name

NAMESPACE = "users"
KEY = "profile"
MAX_FACTS = 12
MAX_TRIPS = 5


def _ns(user_id: str) -> tuple[str, str]:
    return (NAMESPACE, user_id)


def load(store: BaseStore | None, user_id: str | None) -> dict:
    if store is None or not user_id:
        return {}
    item = store.get(_ns(user_id), KEY)
    return dict(item.value) if item else {}


def _save(store: BaseStore | None, user_id: str | None, profile: dict) -> None:
    if store is not None and user_id:
        store.put(_ns(user_id), KEY, profile)


def forget(store: BaseStore, user_id: str) -> None:
    store.delete(_ns(user_id), KEY)


def learn_requirements(store: BaseStore | None, user_id: str | None, req: Requirements) -> None:
    """Запоминает устойчивое о поездках: откуда летает, с кем ездит, что важно."""
    profile = load(store, user_id)
    before = dict(profile)
    if req.origin_code:
        profile["origin_code"] = req.origin_code
    if req.adults:
        profile["party"] = {"adults": req.adults, "children_ages": req.children_ages,
                            "year": date.today().year}
    likes = set(profile.get("likes", []))
    if req.near_sea:
        likes.add("ближе к морю")
    likes |= {f"удобство: {code}" for code in req.must_have}
    if req.district:
        likes.add(f"район: {req.district}")
    profile["likes"] = sorted(likes)
    if profile != before:
        _save(store, user_id, profile)


def remember_fact(store: BaseStore | None, user_id: str | None, fact: str) -> None:
    profile = load(store, user_id)
    facts = [f for f in profile.get("facts", []) if f != fact]
    profile["facts"] = (facts + [fact])[-MAX_FACTS:]
    _save(store, user_id, profile)


def remember_order(store: BaseStore | None, user_id: str | None, order_id: str, summary: str) -> None:
    profile = load(store, user_id)
    trips = [t for t in profile.get("trips", []) if t["order_id"] != order_id]
    profile["trips"] = (trips + [{"order_id": order_id, "summary": summary,
                                  "date": date.today().isoformat()}])[-MAX_TRIPS:]
    _save(store, user_id, profile)


def describe(profile: dict) -> str:
    """Что известно о пользователе — для системного промпта. Пустая строка, если ничего."""
    lines = []
    if code := profile.get("origin_code"):
        lines.append(f"Обычно летит из города {city_name(code)} ({code}).")
    if party := profile.get("party"):
        grown = date.today().year - party.get("year", date.today().year)
        text = f"Ездит: взрослых {party['adults']}"
        if party.get("children_ages"):
            text += ", дети " + ", ".join(str(a + grown) for a in party["children_ages"]) + " лет (возраст на сегодня)"
        lines.append(text + ".")
    if profile.get("likes"):
        lines.append("Раньше выбирал: " + "; ".join(profile["likes"]) + ".")
    if profile.get("facts"):
        lines.append("Просил запомнить: " + "; ".join(profile["facts"]) + ".")
    for trip in profile.get("trips", []):
        lines.append(f"Прошлый заказ {trip['order_id']} ({trip['date']}): {trip['summary']}.")
    return "\n".join(lines)
