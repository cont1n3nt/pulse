# Pulse

Тревел-агент: ищет авиабилеты через Aviasales (Travelpayouts) с помощью LLM.

## Требования

- Python 3.11 или новее
- [uv](https://docs.astral.sh/uv/)

## Запуск

Из корня проекта:

1. Установите зависимости:
   ```bash
   uv sync
   ```
2. Создайте `.env` из примера и впишите ключи (`LLM_API_KEY`, `TRAVELPAYOUTS_TOKEN`; провайдер и модель настраиваются там же):
   ```bash
   cp .env.example .env
   ```
3. Запустите сервер:
   ```bash
   uv run uvicorn backend.main:app --reload
   ```

Страница будет доступна по адресу http://127.0.0.1:8000.

Проверить агента без веб-сервера можно в терминале:
```bash
uv run python -m docs.cli
```
Подробнее об устройстве агента — в [docs/AGENT.md](docs/AGENT.md).

## Структура

- `backend/` — FastAPI-приложение (`main.py`) и агент (`backend/agent/`).
- `frontend/` — статичная веб-страница.
- `docs/` — документация агента и примеры (CLI, FastAPI).
- `old/` — исходный макет.
- `pyproject.toml` — зависимости.
