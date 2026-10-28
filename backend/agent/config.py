"""Все настройки из окружения в одном месте. Пустое значение в ..env — то же, что не задано."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


# по умолчанию — связка, на которой прогнаны evals (README, «Модель»)
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.smartapi.pro/v1")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "claude-haiku-5-5")
# Запасные модели через запятую: при ошибке основной (лимит 429, сбой провайдера) запрос уходит
# следующей. Тот же провайдер и ключ.
LLM_FALLBACK_MODELS = [m.strip() for m in env("LLM_FALLBACK_MODELS").split(",") if m.strip()]
# Финальный ответ модели — структура (текст, рекомендация, подсказки для кнопок), а не свободный
# текст. Отключите, если модель плохо вызывает инструменты: ответ станет обычным текстом.
STRUCTURED_REPLY = env("STRUCTURED_REPLY", "1") not in ("0", "false", "no")

TRAVELPAYOUTS_TOKEN = env("TRAVELPAYOUTS_TOKEN")
# Яндекс Расписания: настоящее расписание поездов. Без ключа — демо-расписание
YANDEX_RASP_KEY = env("YANDEX_RASP_KEY")
HTTP_TIMEOUT = float(env("HTTP_TIMEOUT", "20"))
# Максимум шагов графа за один запрос: аварийный предохранитель. Один цикл «модель → инструмент»
# занимает 3–4 шага (модель, инструменты, узлы middleware); основной лимит — 10 вызовов модели
# в guardrails.py, он завершает ход корректно. Этот должен быть с запасом выше.
AGENT_MEMORY_MAX_CHARS = int(env("AGENT_MEMORY_MAX_CHARS", "512"))
if AGENT_MEMORY_MAX_CHARS < 1:
    raise ValueError("AGENT_MEMORY_MAX_CHARS must be positive")

AGENT_RECURSION_LIMIT = int(env("AGENT_RECURSION_LIMIT", "60"))

SEED_DIR = Path(env("SEED_DIR", str(Path(__file__).resolve().parent.parent / "seed")))
DATA_DIR = Path(env("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
# Локация агента: папка backend/seed/<LOCATION> с location.json и hotels.json (см. location.py)
LOCATION = env("PULSE_LOCATION", "sochi")


def make_llm(model: str | None = None):
    from langchain_openai import ChatOpenAI

    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY не задан: скопируйте ..env.example в ..env и впишите ключ")

    return ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=model or LLM_MODEL,
        temperature=0,
        timeout=60,
        # расход токенов в потоковом ответе: для логов и LangSmith
        stream_usage=True,
        # повторы делает ModelRetryMiddleware, иначе при 429 ожидание умножается
        max_retries=0,
    )
