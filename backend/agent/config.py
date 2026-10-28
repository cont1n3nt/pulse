"""Все настройки из окружения в одном месте."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/free")
# Запасные модели через запятую: при ошибке основной (лимит 429, сбой провайдера) запрос уходит
# следующей. Тот же провайдер и ключ.
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
# Финальный ответ модели — структура (текст, рекомендация, подсказки для кнопок), а не свободный
# текст. Отключите, если модель плохо вызывает инструменты: ответ станет обычным текстом.
STRUCTURED_REPLY = os.getenv("STRUCTURED_REPLY", "1") not in ("0", "false", "no")

TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN", "")
# Яндекс Расписания: настоящее расписание поездов. Без ключа — демо-расписание
YANDEX_RASP_KEY = os.getenv("YANDEX_RASP_KEY", "")
HTTP_TIMEOUT =float(os.getenv("HTTP_TIMEOUT", "20"))
# Максимум шагов графа за один запрос: аварийный предохранитель. Один цикл «модель → инструмент»
# занимает 3–4 шага (модель, инструменты, узлы middleware); основной лимит — 10 вызовов модели
# в guardrails.py, он завершает ход корректно. Этот должен быть с запасом выше.
AGENT_RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "60"))

SEED_DIR = Path(os.getenv("SEED_DIR", Path(__file__).resolve().parent.parent / "seed"))
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def make_llm(model: str | None = None):
    from langchain_openai import ChatOpenAI

    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY не задан: скопируйте .env.example в .env и впишите ключ")

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
