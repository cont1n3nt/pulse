# Pulse

## Требования

Python 3.10 или новее. Установите зависимости из корня проекта: `python -m pip install -r requirements.txt`.

## Запуск

В терминале из корня проекта выполните команды по порядку:

1. Установите зависимости:
   ```bash
   python -m pip install -r requirements.txt
   ```
2. Запустите сервер:
   ```bash
   python -m uvicorn backend.main:app --reload
   ```

Страница будет доступна по адресу http://127.0.0.1:8000.

## Структура

- `backend/` — FastAPI-приложение.
- `frontend/` — статичная веб-страница.
- `old/` — исходный макет.
- `requirements.txt` — зависимости Python.
