# Агент — как устроен и как подключить

## Для бэка: вся интеграция

```python
from backend.agent import handle_message, get_state, ChatRequest, AgentResponse, TripState

@app.post("/api/chat", response_model=AgentResponse)
def chat(req: ChatRequest):
    return handle_message(req.session_id, req.message)

@app.get("/api/trip/{session_id}", response_model=TripState)
def trip(session_id: str):
    return get_state(session_id)
```

Рабочий пример — `docs/api_example.py`. Роут объявлять через `def`, не `async def`:
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
Агент никогда не отдаёт 500: при сбое приходит блок `{"type": "error", "data": {"detail": "agent_failed"}}`.
Подробности ошибки (стек) пишутся только в лог сервера, клиенту они не уходят.

Ограничения текущей версии:
- Сессии и история диалога хранятся в памяти процесса: после перезапуска (в т.ч. `--reload`) они теряются.
  Запускать нужно одним воркером. Запросы внутри одной сессии обрабатываются по очереди.
- Нужна модель с поддержкой tool calling, иначе агент ответит текстом без поиска.
- Цены Aviasales берутся из кэша и ориентировочные; наличие багажа неизвестно.

## Структура

```
backend/
  main.py          FastAPI-приложение (роуты /api/chat и /api/trip подключает бэк)
  agent/
  __init__.py      публичный интерфейс — бэк импортирует только отсюда
  contracts.py     ДОГОВОР: TripState, FlightOption, Block, AgentResponse (менять вдвоём)
  service.py       handle_message: агент → блоки → состояние
  config.py        env + make_llm (OpenAI-совместимый)
  prompts.py       системный промпт
  reference.py     справочники городов/авиакомпаний → backend/seed/ (скачиваются при первом обращении)
  adapters/        сырой API → стандартные модели (по файлу на источник)
  tools/           инструменты агента
docs/
  cli.py           терминал — вызывает ту же handle_message
  api_example.py   пример FastAPI для бэка
```

Файлы внутри `agent/` в схеме выше сдвинуты на один уровень для краткости: на деле они лежат в `backend/agent/`.

## Как добавить новый источник (например, отели)

1. `adapters/hotels.py` → функция `fetch_hotels(...) -> list[HotelOption]`.
2. `tools/hotels.py` → `@tool(response_format="content_and_artifact")`,
   возвращает `(короткий текст для модели, [h.model_dump(mode="json") ...])`. Добавить в `TOOLS`.
3. `service.py` → в цикле по `ToolMessage` добавить ветку `m.name == "search_hotels"`:
   положить в `state.hotels` и добавить `Block(type="hotels", ...)`.

Бэк и фронт при этом ничего не меняют.

## Запуск

Из корня проекта:

```
uv sync
cp .env.example .env    # вписать ключи (LLM_API_KEY, TRAVELPAYOUTS_TOKEN)
uv run python -m docs.cli
```

Без uv: `python -m pip install -r requirements.txt`, затем `python -m docs.cli`.
