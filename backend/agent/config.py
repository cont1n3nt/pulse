"""Все настройки из окружения в одном месте."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "openrouter/free")

TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN", "")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
# Максимум шагов агента за один запрос (защита от зацикливания на инструментах).
AGENT_RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "12"))

SEED_DIR = Path(os.getenv("SEED_DIR", Path(__file__).resolve().parent.parent / "seed"))


def make_llm():
    from langchain_openai import ChatOpenAI

    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY не задан: скопируйте .env.example в .env и впишите ключ")

    return ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        temperature=0,
        timeout=60,
        max_retries=2,
    )
