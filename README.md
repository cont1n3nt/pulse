# Pulse

Тревел-агент: ищет авиабилеты через Aviasales (Travelpayouts) с помощью LLM.

## Требования

- Python 3.11 или новее
- [uv](https://docs.astral.sh/uv/) (только для варианта 1)

## Запуск

Все команды выполняются из корня проекта. Выберите один из двух способов.

### Вариант 1: uv

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

### Вариант 2: pip и requirements.txt

1. Создайте виртуальное окружение и активируйте его:
   ```bash
   python -m venv .venv
   ```
   Windows (PowerShell): `.venv\Scripts\Activate.ps1`, Linux и macOS: `source .venv/bin/activate`.
2. Установите зависимости:
   ```bash
   python -m pip install -r requirements.txt
   ```
3. Создайте `.env` (как в варианте 1) и запустите сервер:
   ```bash
   python -m uvicorn backend.main:app --reload
   ```

Страница будет доступна по адресу http://127.0.0.1:8000.

Проверить агента без веб-сервера можно в терминале (при варианте 2 вместо `uv run` пишите `python`):
```bash
uv run python -m docs.cli
```

`requirements.txt` дублирует список из `pyproject.toml`: при изменении зависимостей обновляйте оба файла.
Подробнее об устройстве агента — в [docs/AGENT.md](docs/AGENT.md).

## Структура

- `backend/` — FastAPI-приложение (`main.py`) и агент (`backend/agent/`).
- `frontend/` — статичная веб-страница.
- `docs/` — документация агента и примеры (CLI, FastAPI).
- `old/` — исходный макет.
- `pyproject.toml` — зависимости.
