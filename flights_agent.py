import json
import os
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

TP_TOKEN = os.environ["TRAVELPAYOUTS_TOKEN"]
HTTP_TIMEOUT = 20
SEED_DIR = Path("seed")
SEED_DIR.mkdir(exist_ok=True)


def load_reference(name: str) -> list[dict]:
    """Прочитать справочник из seed/, а если его нет — скачать и сохранить."""
    path = SEED_DIR / f"{name}_ru.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(
            f"https://api.travelpayouts.com/data/ru/{name}.json",
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  [seed] справочник {name} сохранён в {path}")
        return data
    except requests.RequestException as exc:
        print(f"  [seed] не удалось скачать {name}: {type(exc).__name__}")
        return []


CITIES = load_reference("cities")
AIRLINES = {a["code"]: a.get("name") or a["code"] for a in load_reference("airlines") if a.get("code")}


def normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е").replace(" ", "-")


@tool
def resolve_city(name: str) -> list[dict] | str:
    """Найти IATA-код города по названию.

    Вызывай перед search_flights, чтобы получить коды городов
    (например, «Москва» -> MOW, «Нью-Йорк» -> NYC).

    Args:
        name: название города, как его написал пользователь.
    """
    query = normalize(name)

    matches = []
    for c in CITIES:
        names = [c.get("name") or ""] + list((c.get("name_translations") or {}).values())
        normalized = [normalize(n) for n in names if n]
        if query in normalized:
            score = 0
        elif any(n.startswith(query) for n in normalized):
            score = 1
        else:
            continue
        # города с действующим аэропортом — в приоритете
        if not c.get("has_flightable_airport", True):
            score += 2
        matches.append((score, c))

    if matches:
        matches.sort(key=lambda m: m[0])
        return [
            {"code": c["code"], "name": c.get("name"), "country": c.get("country_code")}
            for _, c in matches[:3]
        ]
    return "Город не найден. Попроси пользователя уточнить название."


@tool
def search_flights(
    origin: str,
    destination: str,
    departure: str,
    return_date: str | None = None,
    limit: int = 5,
) -> list[dict] | str:
    """Найти самые дешёвые перелёты с реальными ценами Aviasales.

    Args:
        origin: IATA-код города вылета (из resolve_city), например MOW.
        destination: IATA-код города прилёта, например NYC.
        departure: месяц YYYY-MM или точная дата YYYY-MM-DD.
        return_date: дата/месяц обратного вылета, если нужен билет туда-обратно.
        limit: сколько вариантов вернуть (1–10).
    """
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": departure,
        "currency": "rub",
        "sorting": "price",
        "limit": max(1, min(limit, 10)),
    }
    if return_date:
        params["return_at"] = return_date

    try:
        resp = requests.get(
            "https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
            params=params,
            headers={"X-Access-Token": TP_TOKEN},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException as exc:
        return f"Сервис цен Aviasales сейчас недоступен ({type(exc).__name__}). Скажи пользователю честно, цены не выдумывай."

    if not data:
        return "Билетов в кэше Aviasales не найдено. Предложи другие даты или соседний месяц."

    return [
        {
            "airline": AIRLINES.get(t["airline"], t["airline"]),
            "flight": f'{t["airline"]} {t["flight_number"]}',
            "from_airport": t["origin_airport"],
            "to_airport": t["destination_airport"],
            "departure_at": t["departure_at"],
            "duration_min": t.get("duration_to") or t.get("duration"),
            "transfers": t.get("transfers", 0),
            "price_rub": t["price"],
            "link": "https://www.aviasales.ru" + t["link"],
        }
        for t in data
    ]


# ---------- модель и агент ----------
SYSTEM_PROMPT = f"""Ты — тревел-агент Островка. Сегодня {date.today().isoformat()}.

Правила:
- Сначала получи IATA-коды городов через resolve_city, затем ищи через search_flights.
- Цены, время и рейсы бери ТОЛЬКО из результатов инструментов. Никогда не выдумывай их.
- Если не хватает города вылета или назначения — задай один короткий вопрос.
  Если не указаны даты — ищи на ближайший месяц и скажи об этом как о допущении.
- В ответе: 2–3 лучших варианта, для каждого — почему подходит, чем жертвуем
  (пересадки, время в пути, ночной вылет) и риски (багаж может быть не включён).
- Отвечай по-русски, кратко и по делу.
"""

llm = ChatOpenAI(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    model=os.environ["LLM_MODEL"],
    temperature=0,
)

agent = create_agent(
    model=llm,
    tools=[resolve_city, search_flights],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),
)


def message_text(msg) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def main() -> None:
    config = {"configurable": {"thread_id": "terminal"}}
    print(f"Справочник городов: {len(CITIES)} записей, авиакомпаний: {len(AIRLINES)}")
    print("Тревел-агент. Пример: Москва — Нью-Йорк в декабре. Выход: exit")
    while True:
        user = input("\nТы: ").strip()
        if user.lower() in {"exit", "quit", "выход"}:
            break
        if not user:
            continue
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": user}]}, config)
        except Exception as exc:  # чтобы сбой модели не убивал весь диалог
            print(f"\n[ошибка] {type(exc).__name__}: {exc}")
            continue

        for m in result["messages"]:
            for call in getattr(m, "tool_calls", None) or []:
                print(f"  [tool] {call['name']}({call['args']})")

        print("\nАгент:", message_text(result["messages"][-1]))


if __name__ == "__main__":
    main()
