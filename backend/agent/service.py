import json
import logging
import re
import sqlite3
import threading
import time
from functools import lru_cache
from typing import Annotated

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolRetryMiddleware,
    after_model,
    dynamic_prompt,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_config
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command
from typing_extensions import NotRequired

from . import memory
from .adapters import hotels as hotels_db
from .adapters import photos
from .adapters.aviasales import SourceUnavailable
from .config import AGENT_RECURSION_LIMIT, DATA_DIR, LLM_FALLBACK_MODELS, LLM_MODEL, STRUCTURED_REPLY, make_llm
from .contracts import AgentReply, AgentResponse, Block, TripState
from .guardrails import default_guards, inspect_text
from .observability import LoggingMiddleware
from .prompts import system_prompt
from .tools.flights import TOOLS as FLIGHT_TOOLS
from .tools.hotels import TOOLS as HOTEL_TOOLS
from .tools.packages import TOOLS as PACKAGE_TOOLS
from .tools.aftersale import TOOLS as AFTERSALE_TOOLS
from .tools.trip import TOOLS as TRIP_TOOLS
from .tools._state import cart_block
from .trip import reduce_trip, summary

log = logging.getLogger(__name__)

TOOLS = PACKAGE_TOOLS + HOTEL_TOOLS + TRIP_TOOLS + AFTERSALE_TOOLS + FLIGHT_TOOLS
REPLY_TOOL = AgentReply.__name__
# сетевые инструменты: временный сбой источника повторяется, потом модель получает объяснение
NETWORK_TOOLS = ["search_flights"]
FAILED = "Не получилось обработать запрос, попробуйте ещё раз."

_SESSION_LOCKS: dict[str, threading.Lock] = {}
_LOCK = threading.Lock()


class TripAgentState(AgentState):
    """История диалога + состояние поездки. Оба сохраняются чекпоинтером в SQLite."""
    trip: NotRequired[Annotated[dict, reduce_trip]]


def _trip(values: dict) -> TripState:
    return TripState.model_validate(values.get("trip") or {})


@dynamic_prompt
def _prompt(request) -> str:
    # Вызывается на каждый шаг: дата, память о пользователе и состояние поездки всегда актуальны
    text = system_prompt()
    user_id = get_config()["configurable"].get("user_id")
    if profile := memory.describe(memory.load(request.runtime.store, user_id)):
        text += ("\n\nЧто известно о пользователе из прошлых чатов (используй как допущения по умолчанию "
                 "и назови их; условия этой поездки важнее):\n" + profile)
    return text + "\n\nСостояние поездки (источник истины):\n" + summary(_trip(request.state))


def _connect(name: str, **kwargs) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATA_DIR / name, check_same_thread=False, **kwargs)


def _checkpointer() -> SqliteSaver:
    # структурированный ответ лежит в состоянии графа: разрешаем чекпоинтеру восстанавливать его тип
    serde = JsonPlusSerializer(allowed_msgpack_modules=[(AgentReply.__module__, AgentReply.__name__)])
    return SqliteSaver(_connect("agent_memory.db"), serde=serde)


@lru_cache
def _store() -> SqliteStore:
    """Долговременная память о пользователях: общая для всех чатов пользователя."""
    # хранилище само открывает транзакции (BEGIN), поэтому соединение — в режиме autocommit
    store = SqliteStore(_connect("user_memory.db", isolation_level=None))
    store.setup()
    return store


def _network_failure(exc: Exception) -> str:
    return (f"Источник данных недоступен ({type(exc).__name__}), повторы не помогли. "
            "Скажи пользователю честно и ничего не выдумывай; продолжай с тем, что есть.")


def _resilience() -> list:
    """Запасные модели и повтор вызова модели; повтор сетевых инструментов."""
    middleware = []
    if LLM_FALLBACK_MODELS:
        # внешний слой: основная модель с повтором, при неудаче — следующая из списка
        middleware.append(ModelFallbackMiddleware(*[make_llm(m) for m in LLM_FALLBACK_MODELS]))
    middleware += [
        ModelRetryMiddleware(max_retries=2, initial_delay=2.0, on_failure="error"),
        ToolRetryMiddleware(max_retries=2, tools=NETWORK_TOOLS, retry_on=(SourceUnavailable,),
                            on_failure=_network_failure, initial_delay=0.5),
    ]
    return middleware


@lru_cache
def _agent():
    return create_agent(
        model=make_llm(),
        tools=TOOLS,
        state_schema=TripAgentState,
        middleware=[*default_guards(), *_resilience(), LoggingMiddleware(), _prompt, _clean_pseudo_reply],
        # короткий ответ инструмента: иначе модель видит в истории repr ответа и начинает его копировать
        response_format=ToolStrategy(AgentReply, tool_message_content="Ответ показан пользователю.")
        if STRUCTURED_REPLY else None,
        checkpointer=_checkpointer(),
        store=_store(),
    )


def _config(session_id: str, user_id: str | None = None) -> dict:
    # run_name, tags и metadata видны в трассировке LangSmith, если она включена в окружении
    return {"configurable": {"thread_id": session_id, "user_id": user_id},
            "recursion_limit": AGENT_RECURSION_LIMIT, "run_name": "pulse-agent",
            "tags": ["pulse", LLM_MODEL], "metadata": {"chat_id": session_id, "user_id": user_id}}


def _session_lock(session_id: str) -> threading.Lock:
    with _LOCK:
        return _SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _text(msg) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def _pending_checkout(session_id: str):
    """Ожидающий подтверждения заказ (interrupt инструмента checkout) или None."""
    snapshot = _agent().get_state(_config(session_id))
    return next((i for i in snapshot.interrupts if i.value.get("type") == "checkout"), None)


def get_state(session_id: str) -> TripState:
    snapshot = _agent().get_state(_config(session_id))
    trip = _trip(snapshot.values)
    if any(i.value.get("type") == "checkout" for i in snapshot.interrupts):
        trip.stage = "checkout"
    return trip


def reset_session(session_id: str) -> None:
    _agent().checkpointer.delete_thread(session_id)
    with _LOCK:
        _SESSION_LOCKS.pop(session_id, None)


# --- структурированный ответ ----------------------------------------------------------------

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "r": "", "b": "", "f": ""}


def partial_text(args: str) -> str:
    """Значение поля "text" из недописанного JSON аргументов: чтобы печатать ответ по мере генерации."""
    found = re.search(r'"text"\s*:\s*"', args)
    if not found:
        return ""
    out, i = [], found.end()
    while i < len(args):
        ch = args[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(args):
            break
        code = args[i + 1]
        if code == "u":
            if i + 6 > len(args):
                break
            out.append(chr(int(args[i + 2:i + 6], 16)))
            i += 6
        else:
            out.append(_ESCAPES.get(code, code))
            i += 2
    return "".join(out)


class _ReplyStream:
    """Собирает аргументы вызова AgentReply из потока: текст печатается по мере генерации,
    а рекомендация и подсказки уходят фронту блоком в конце."""

    def __init__(self):
        self.calls: dict[tuple, dict] = {}  # (id сообщения, index) -> {"name", "args", "sent"}

    def feed(self, chunk: AIMessageChunk, shown=lambda message_id: "") -> str:
        """Новый кусок текста ответа. shown(id) закрывает обычный текст того же сообщения и возвращает,
        что из него уже ушло пользователю: некоторые модели пишут ответ и текстом, и в AgentReply."""
        delta = ""
        for part in chunk.tool_call_chunks or []:
            call = self.calls.setdefault((chunk.id, part.get("index")), {"name": None, "args": "", "sent": 0})
            if call["name"] is None and part.get("name"):
                call["name"] = part["name"]
                if call["name"] == REPLY_TOOL:
                    call["base"] = shown(chunk.id)
            call["args"] += part.get("args") or ""
            if call["name"] == REPLY_TOOL:
                text, base = partial_text(call["args"]), call.get("base", "")
                if base and base.startswith(text):
                    continue  # повторяет уже показанное
                if base and text.startswith(base):
                    call["sent"] = max(call["sent"], len(base))
                elif base:
                    call["base"] = ""  # текст разошёлся с показанным: печатаем ответ как есть
                delta += text[call["sent"]:]
                call["sent"] = len(text)
        return delta

    def whole(self, message: AIMessage) -> str:
        """Ответ пришёл одним сообщением (модель без стриминга)."""
        for call in message.tool_calls:
            if call["name"] == REPLY_TOOL:
                self.calls[(message.id, "whole")] = {"name": REPLY_TOOL, "args": json.dumps(call["args"]), "sent": 0}
                return call["args"].get("text", "")
        return ""

    def reply(self) -> dict | None:
        for call in reversed(list(self.calls.values())):
            if call["name"] == REPLY_TOOL:
                try:
                    return AgentReply.model_validate_json(call["args"]).model_dump()
                except ValueError:
                    return None
        return None


# --- ответ, написанный текстом вместо вызова -------------------------------------------------
#
# Финальный ответ — вызов инструмента AgentReply, а слабые в tool calling модели иногда пишут
# этот вызов обычным текстом, каждая по-своему:
#   Gemma:   «…Берём Оптимум?", recommendation="optimum", suggestions=[…])» или «AgentReply(text="…")»
#   gpt-oss: голые аргументы {"recommendation": …, "suggestions": […], "text": "…"}
#   прочие:  ```json {…}``` или {"name": "AgentReply", "arguments": {…}}
# Такой текст вырезаем из потока и из истории, а текст ответа, рекомендацию и подсказки достаём
# из него, чтобы пользователь и фронт получили их как обычно.

PSEUDO_CALL = REPLY_TOOL + "("
_PSEUDO_TAIL = re.compile(r'["\']\s*,\s*recommendation\s*=')
_JSON_REPLY = re.compile(r'(?:```(?:json)?\s*)?\{\s*(?:"(?:text|recommendation|suggestions)"\s*:|"name"\s*:\s*"'
                         + REPLY_TOOL + '")')


def _read_string(text: str, start: int) -> str:
    """Строковый литерал Python/JSON, начиная с открывающей кавычки в позиции start."""
    quote, out, i = text[start], [], start + 1
    while i < len(text) and text[i] != quote:
        if text[i] == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _pseudo_start(content: str) -> int:
    """Где в тексте начинается вызов AgentReply, написанный словами; -1, если его нет."""
    found = [i for i in (content.find(PSEUDO_CALL),) if i >= 0]
    for pattern in (_PSEUDO_TAIL, _JSON_REPLY):
        if match := pattern.search(content):
            found.append(match.start())
    return min(found) if found else -1


def _json_reply(call: str) -> tuple[str, str | None, list[str]]:
    """(text, recommendation, suggestions) из JSON-ответа, в том числе недописанного или битого."""
    try:
        data = json.loads(call[call.find("{"):call.rfind("}") + 1])
        data = data.get("arguments") or data.get("parameters") or data if isinstance(data, dict) else {}
        data = json.loads(data) if isinstance(data, str) else data
        reply = AgentReply.model_validate(data)
        return reply.text, reply.recommendation, reply.suggestions
    except ValueError:
        rec = re.search(r'"recommendation"\s*:\s*"(\w+)"', call)
        suggestions = []
        if found := re.search(r'"suggestions"\s*:\s*\[(.*?)\]', call, re.S):
            suggestions = re.findall(r'"((?:[^"\\]|\\.)*)"', found.group(1))
        return partial_text(call), rec.group(1) if rec else None, suggestions


def split_pseudo_reply(content: str) -> tuple[str, dict | None]:
    """(текст для пользователя, {"text", "recommendation", "suggestions"} или None)."""
    start = _pseudo_start(content)
    if start < 0:
        return content, None
    call = content[start:]
    clean = content[:start].rstrip()
    if _JSON_REPLY.match(call):
        text, rec, suggestions = _json_reply(call)
    else:
        text = ""
        if found := re.search(r'\btext\s*=\s*(["\'])', call):
            text = _read_string(call, found.start(1))
        found = re.search(r'recommendation\s*=\s*["\']?(\w+)', call)
        rec = found.group(1) if found and found.group(1) != "None" else None
        suggestions = []
        if found := re.search(r"suggestions\s*=\s*\[(.*?)\]", call, re.S):
            suggestions = [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', found.group(1))]
        if PSEUDO_CALL not in call and not clean.endswith(("?", ".", "!")):
            clean = clean.rstrip("\"'")  # хвост без имени: текст ответа сам был аргументом text
    clean = clean.strip() or text.strip()
    return clean, {"text": clean, "recommendation": rec, "suggestions": suggestions}


@after_model
def _clean_pseudo_reply(state, runtime):
    """Чистит историю: иначе модель увидит свой «текстовый вызов» и станет повторять его."""
    last = state["messages"][-1]
    if last.type != "ai":
        return None
    if last.tool_calls:
        # ответ и текстом, и в AgentReply: в истории оставляем только вызов, без копии текста
        if _text(last) and any(c["name"] == REPLY_TOOL for c in last.tool_calls):
            return {"messages": [last.model_copy(update={"content": ""})]}
        return None
    clean, reply = split_pseudo_reply(_text(last))
    if reply is None:
        return None
    log.info("Модель написала AgentReply текстом, а не вызовом: ответ разобран и очищен")
    return {"messages": [AIMessage(content=clean, id=last.id)]}


class _ContentStream:
    """Текст модели в поток: придерживает хвост, похожий на начало текстового вызова, и обрезает его."""

    HOLD = len(PSEUDO_CALL) + 24  # столько символов в конце держим, пока не ясно, вызов это или нет

    def __init__(self):
        self.text: dict[str, str] = {}  # id сообщения -> весь текст
        self.sent: dict[str, int] = {}
        self.closed: set[str] = set()
        self.inline: dict[str, int] = {}  # id сообщения -> сколько текста из JSON-ответа уже напечатано

    def feed(self, message_id: str, token: str) -> str:
        text = self.text[message_id] = self.text.get(message_id, "") + token
        if message_id in self.closed:
            return ""
        start = _pseudo_start(text)
        if start < 0:
            return self._emit(message_id, max(0, len(text) - self.HOLD))
        out = self._emit(message_id, len(text[:start].rstrip()))
        if not text[:start].strip() and _JSON_REPLY.match(text, start):
            # всё сообщение — JSON-ответ: печатаем его поле text по мере генерации
            streamed = partial_text(text[start:])
            out += streamed[self.inline.get(message_id, 0):]
            self.inline[message_id] = len(streamed)
        return out

    def flush(self) -> str:
        """Конец хода: отдаём придержанное; из текстового вызова — только его текст, если другого не было."""
        out = ""
        for message_id, text in self.text.items():
            if message_id in self.closed:
                continue
            clean, reply = split_pseudo_reply(text)
            if reply is None:
                out += self._emit(message_id, len(text))
            elif message_id in self.inline:
                out += clean[self.inline[message_id]:]
                self.inline[message_id] = max(self.inline[message_id], len(clean))
            elif not self.sent.get(message_id):
                out += clean
                self.sent[message_id] = len(text)
        return out

    def close(self, message_id: str) -> str:
        """Дальше текст этого сообщения не печатаем (его заменит AgentReply); возвращает уже показанное."""
        self.closed.add(message_id)
        return self.text.get(message_id, "")[:self.sent.get(message_id, 0)]

    def seen(self, text: str) -> bool:
        return any(known.strip() == text.strip() for known in self.text.values())

    def replies(self) -> list[dict]:
        return [reply for text in self.text.values() if (reply := split_pseudo_reply(text)[1])]

    def _emit(self, message_id: str, limit: int) -> str:
        sent = self.sent.get(message_id, 0)
        if limit <= sent:
            return ""
        self.sent[message_id] = limit
        return self.text[message_id][sent:limit]


# --- последняя чистка текста -----------------------------------------------------------------
#
# Промпт запрещает разметку и внутренние id, но модели всё равно иногда пишут **Эконом** или
# «пакет optimum». Чат показывает текст как есть, поэтому чистим поток кодом, по целым словам.

_LABELS = {"economy": "Эконом", "optimum": "Оптимум", "comfort": "Комфорт"}
_TIDY = [
    (re.compile(r"\*\*|__"), ""),
    (re.compile(r"(?<=\n)#{1,3} "), ""),
    (re.compile(r"(?<=\n)[ \t]*[-*•] "), "— "),
    (re.compile(r"\b(" + "|".join(_LABELS) + r")\b"), lambda m: _LABELS[m.group(1)]),
]


class _Tidy:
    """Пропускает текст по целым словам, чтобы правила видели «**Эконом**» и «optimum» целиком."""

    def __init__(self):
        self.pending = ""
        self.last = "\n"  # начало ответа — тоже начало строки

    def feed(self, token: str) -> str:
        self.pending += token
        cut = max(self.pending.rfind(" "), self.pending.rfind("\n")) + 1
        return self._emit(cut)

    def flush(self) -> str:
        return self._emit(len(self.pending))

    def _emit(self, cut: int) -> str:
        if cut <= 0:
            return ""
        text, self.pending = self.last + self.pending[:cut], self.pending[cut:]
        for pattern, repl in _TIDY:
            text = pattern.sub(repl, text)
        text, self.last = text[1:], text[-1]
        return text


def _tidy_tokens(events):
    tidy = _Tidy()
    for event in events:
        if event.get("type") == "token":
            if text := tidy.feed(event["text"]):
                yield {"type": "token", "text": text}
            continue
        if text := tidy.flush():
            yield {"type": "token", "text": text}
        yield event
    if text := tidy.flush():
        yield {"type": "token", "text": text}


def _run(session_id: str, payload, user_id: str | None = None):
    """Прогон графа как поток событий SSE: reasoning -> (token | block | tool)* -> confirm? -> done."""
    config = _config(session_id, user_id)
    replies = _ReplyStream()
    content = _ContentStream()
    yield {"type": "reasoning"}
    for mode, data in _agent().stream(payload, config, stream_mode=["messages", "custom"]):
        if mode == "custom":
            yield data
            continue
        chunk, meta = data
        if isinstance(chunk, ToolMessage):
            if chunk.name != REPLY_TOOL:
                if token := content.flush():
                    yield {"type": "token", "text": token}
                yield {"type": "tool", "name": chunk.name}
        elif isinstance(chunk, AIMessageChunk):
            if meta.get("langgraph_node") != "model":
                continue
            if (token := _text(chunk)) and (token := content.feed(chunk.id, token)):
                yield {"type": "token", "text": token}
            if token := replies.feed(chunk, content.close):
                yield {"type": "token", "text": token}
        elif isinstance(chunk, AIMessage):
            # ответ целиком (отказ фильтра, модель без стриминга); уже показанный потоком — пропускаем
            if (text_out := _text(chunk)) and not content.seen(text_out):
                if token := content.feed(chunk.id or "whole", text_out):
                    yield {"type": "token", "text": token}
            elif text_out := replies.whole(chunk):
                yield {"type": "token", "text": text_out}
    if token := content.flush():
        yield {"type": "token", "text": token}
    if reply := replies.reply() or next(iter(content.replies()[-1:]), None):
        yield {"type": "block", "block": "reply", "recommendation": reply["recommendation"],
               "suggestions": reply["suggestions"][:3]}
    if confirm := pending_checkout(session_id):
        yield confirm


def pending_checkout(session_id: str) -> dict | None:
    """Событие confirm для заказа, который ждёт кнопки, или None: чтобы восстановить карточку после перезагрузки."""
    pending = _pending_checkout(session_id)
    return {**pending.value, "type": "confirm", "action": "checkout"} if pending else None


def _guarded(session_id: str, events):
    """Один запрос на сессию за раз; ошибки не уходят клиенту, только в лог."""
    started = time.perf_counter()
    log.info("Ход агента: чат %s", session_id)
    try:
        with _session_lock(session_id):
            yield from _tidy_tokens(events())
    except Exception:
        log.exception("Агент упал на сессии %s через %.1f с", session_id, time.perf_counter() - started)
        yield {"type": "error", "detail": FAILED}
        return
    log.info("Ход агента: чат %s, готово за %.1f с", session_id, time.perf_counter() - started)
    yield {"type": "done"}


def stream_message(session_id: str, text: str, user_id: str | None = None):
    """Генератор событий SSE на сообщение пользователя. user_id — ключ долговременной памяти."""

    def events():
        pending = _pending_checkout(session_id)
        if pending is None:
            yield from _run(session_id, {"messages": [{"role": "user", "content": text}]}, user_id)
            return
        # Пользователь написал, не нажав кнопку оплаты: это отказ от оплаты, а текст уходит
        # модели вместе с отказом. Фильтр проверяем сами: в обход входного middleware.
        message = None if inspect_text(text) else text
        yield from _run(session_id, Command(resume={"approved": False, "message": message}), user_id)

    yield from _guarded(session_id, events)


def resume_checkout(session_id: str, approved: bool, user_id: str | None = None):
    """Генератор событий SSE на нажатие «Оплатить» или «Отмена»."""

    def events():
        if _pending_checkout(session_id) is None:
            yield {"type": "error", "detail": "Нет заказа, ожидающего подтверждения."}
            return
        yield from _run(session_id, Command(resume={"approved": approved}), user_id)

    yield from _guarded(session_id, events)


def get_memory(user_id: str) -> dict:
    """Что агент помнит о пользователе: для профиля, где это можно посмотреть и стереть."""
    profile = memory.load(_store(), user_id)
    return {"profile": profile, "text": memory.describe(profile)}


def forget_memory(user_id: str) -> None:
    memory.forget(_store(), user_id)


def hotel_photo(hotel_id: str) -> dict | None:
    """Фото отеля для карточки: {"path", "source", "credit", "page"} или None."""
    hotel = hotels_db.get(hotel_id)
    if hotel is None:
        return None
    entry = photos.photo(hotel)
    return entry and {"path": photos.path(entry), "source": entry["source"], "credit": entry["credit"],
                      "page": entry["page"]}


def trip_view(session_id: str) -> dict:
    """Всё, что фронт показывает в панели поездки: для восстановления после перезагрузки."""
    trip = get_state(session_id)
    return {
        "stage": trip.stage, "orders": trip.orders,
        "hotels": [h.model_dump(mode="json") for h in trip.hotels],
        "flights": [f.model_dump(mode="json") for f in trip.flights],
        "packages": [p.model_dump(mode="json") for p in trip.packages],
        "weather": trip.weather,
        "cart": cart_block(trip),
        "confirm": pending_checkout(session_id),
    }


def collect(session_id: str, events) -> AgentResponse:
    """Собирает поток событий в один ответ (для CLI, синхронного API и прогонов сценариев)."""
    message, blocks, tool_calls = "", [], []
    for event in events:
        if event["type"] == "token":
            message += event["text"]
        elif event["type"] == "tool":
            tool_calls.append(event["name"])
        elif event["type"] == "block":
            blocks.append(Block(type=event["block"], data={k: v for k, v in event.items()
                                                          if k not in ("type", "block")}))
        elif event["type"] == "confirm":
            blocks.append(Block(type="confirm", data={k: v for k, v in event.items() if k != "type"}))
        elif event["type"] == "error":
            message = message or event["detail"]
            blocks.append(Block(type="error", data={"detail": "agent_failed"}))
    try:
        state = get_state(session_id)
    except Exception:
        log.exception("Не удалось прочитать состояние сессии %s", session_id)
        state = TripState()
    return AgentResponse(session_id=session_id, message=message, blocks=blocks,
                         state=state, tool_calls=tool_calls)


def handle_message(session_id: str, text: str, user_id: str | None = None) -> AgentResponse:
    return collect(session_id, stream_message(session_id, text, user_id))
