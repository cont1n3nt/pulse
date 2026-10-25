# Агент — как устроен и как подключить

## Для бэка: вся интеграция

```python
from agent import handle_message, get_state, ChatRequest, AgentResponse, TripState

@app.post("/api/chat", response_model=AgentResponse)
def chat(req: ChatRequest):
    return handle_message(req.session_id, req.message)

@app.get("/api/trip/{session_id}", response_model=TripState)
def trip(session_id: str):
    return get_state(session_id)
```

Рабочий пример — `api_example.py`. Роут объявлять через `def`, не `async def`:
агент синхронный, FastAPI сам вынесет его в пул потоков.

Ответ `/api/chat`:
```json
{
  "session_id": "abc",
  "message": "Самый дешёвый — ЮТэйр 22 ноября за 2406 ₽…",
  "blocks": [{"type": "flights", "data": {"items": [ {FlightOption}, … ]}}],
  "state": {TripState},
  "tool_calls": ["resolve_city", "search_flights"]
}
```

`message` — текст в чат, `blocks` — что рисовать карточками, `state` — текущий план.
Агент никогда не отдаёт 500: при сбое приходит блок `{"type": "error"}`.

## Структура

```
agent/
  __init__.py      публичный интерфейс — бэк импортирует только отсюда
  contracts.py     ДОГОВОР: TripState, FlightOption, Block, AgentResponse (менять вдвоём)
  service.py       handle_message: агент → блоки → состояние
  config.py        env + make_llm (OpenAI-совместимый)
  prompts.py       системный промпт
  reference.py     справочники городов/авиакомпаний → seed/
  adapters/        сырой API → стандартные модели (по файлу на источник)
  tools/           инструменты агента
cli.py             терминал — вызывает ту же handle_message
api_example.py     пример FastAPI для бэка
```

## Как добавить новый источник (например, отели)

1. `adapters/hotels.py` → функция `fetch_hotels(...) -> list[HotelOption]`.
2. `tools/hotels.py` → `@tool(response_format="content_and_artifact")`,
   возвращает `(короткий текст для модели, [h.model_dump(mode="json") ...])`. Добавить в `TOOLS`.
3. `service.py` → в цикле по `ToolMessage` добавить ветку `m.name == "search_hotels"`:
   положить в `state.hotels` и добавить `Block(type="hotels", ...)`.

Бэк и фронт при этом ничего не меняют.

## Запуск

```
uv add "langchain[openai]" python-dotenv requests
cp .env.example .env    # вписать ключи
uv run cli.py
```
