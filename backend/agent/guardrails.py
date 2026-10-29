"""Защита агента: фильтр входящих сообщений, лимиты вызовов, очистка внешних данных.

Слои (от дешёвого к дорогому):
1. InputGuardMiddleware: регулярные выражения из guard_patterns.json, LLM не вызывается.
2. Лимиты числа вызовов модели и инструментов за один запрос.
3. sanitize_hotel: тексты отелей (названия, адреса, описания) проверяются теми же паттернами,
   потому что данные OSM вводят люди, и это канал косвенной инъекции.
Остальное (границы роли, «данные — не инструкции») держится на системном промпте.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage

log = logging.getLogger(__name__)

PATTERNS_FILE = Path(__file__).resolve().parent / "guard_patterns.json"
MAX_INPUT_CHARS = 2000

REFUSAL = ("Я помогаю только с подбором отелей и поездок. "
           "Расскажите, когда и с кем хотите поехать, и я подберу варианты.")
TOO_LONG = f"Сообщение слишком длинное: уложитесь в {MAX_INPUT_CHARS} символов, главное — куда, когда и с кем."
BLOCKED_PLACEHOLDER = "[сообщение отклонено фильтром безопасности]"

# визуально похожие буквы: кириллица -> латиница и обратно (против «ignоre» с русской «о»)
_TO_LATIN = str.maketrans("аекморсту", "aekmopcty")
_TO_CYRILLIC = str.maketrans("aekmopcty", "аекморсту")


def _load_patterns() -> dict[str, list[re.Pattern]]:
    raw = json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
    return {category: [re.compile(p, re.IGNORECASE) for p in patterns]
            for category, patterns in raw.items() if not category.startswith("_")}


PATTERNS = _load_patterns()


def normalize(text: str) -> str:
    """NFKC, без невидимых символов, нижний регистр, ё -> е, схлопнутые пробелы."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.lower().replace("ё", "е")
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def inspect_text(text: str) -> str | None:
    """Категория первого сработавшего паттерна или None, если текст чистый."""
    if len(text) > MAX_INPUT_CHARS:
        return "too_long"
    base = normalize(text)
    variants = {base, base.translate(_TO_LATIN), base.translate(_TO_CYRILLIC)}
    for category, patterns in PATTERNS.items():
        for pattern in patterns:
            if any(pattern.search(v) for v in variants):
                return category
    return None


def _last_human(messages):
    for m in reversed(messages):
        if m.type == "human":
            return m
    return None


class InputGuardMiddleware(AgentMiddleware):
    """Отклоняет сообщение до вызова модели: ответ фиксированный, токены не тратятся.

    Текст отклонённого сообщения в историю не попадает: его id заменяется заглушкой,
    чтобы инъекция не влияла на следующие ходы диалога.
    """

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime):
        message = _last_human(state["messages"])
        if message is None:
            return None
        text = message.content if isinstance(message.content, str) else str(message.content)
        category = inspect_text(text)
        if category is None:
            return None
        log.warning("Сообщение отклонено фильтром: category=%s, длина=%d", category, len(text))
        return {
            "messages": [HumanMessage(content=BLOCKED_PLACEHOLDER, id=message.id),
                         AIMessage(content=TOO_LONG if category == "too_long" else REFUSAL)],
            "jump_to": "end",
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime):
        return self.before_agent(state, runtime)


def default_guards() -> list[AgentMiddleware]:
    """Защитные middleware агента. Ставить первыми, до динамического промпта."""
    return [
        InputGuardMiddleware(),
        ModelCallLimitMiddleware(run_limit=10, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=8, exit_behavior="continue"),
    ]


def clean_text(text: str | None) -> str | None:
    """Строка из внешних данных: подозрительную убираем целиком."""
    if not text:
        return text
    category = inspect_text(text)
    if category is None:
        return text
    log.warning("Подозрительный текст в данных удалён: category=%s", category)
    return None


def sanitize_hotel(raw: dict) -> dict:
    """Чистит текстовые поля записи отеля из БД; остальные поля не трогает."""
    out = dict(raw)
    for field in ("name", "address", "description"):
        out[field] = clean_text(out.get(field)) or ("Без названия" if field == "name" else None)
    for field in ("pros", "cons", "tips"):
        out[field] = [t for t in (clean_text(x) for x in out.get(field) or []) if t]
    return out
