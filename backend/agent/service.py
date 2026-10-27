import logging
import sqlite3
import threading
from functools import lru_cache
from typing import Annotated

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import dynamic_prompt
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from typing_extensions import NotRequired

from .config import AGENT_RECURSION_LIMIT, DATA_DIR, make_llm
from .contracts import AgentResponse, Block, TripState
from .guardrails import default_guards, inspect_text
from .prompts import system_prompt
from .tools.flights import TOOLS as FLIGHT_TOOLS
from .tools.hotels import TOOLS as HOTEL_TOOLS
from .tools.trip import TOOLS as TRIP_TOOLS
from .trip import reduce_trip, summary

log = logging.getLogger(__name__)

TOOLS = HOTEL_TOOLS + TRIP_TOOLS + FLIGHT_TOOLS
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
    # Вызывается на каждый шаг: дата и состояние поездки в промпте всегда актуальны
    return system_prompt() + "\n\nСостояние поездки (источник истины):\n" + summary(_trip(request.state))


def _checkpointer() -> SqliteSaver:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "agent_memory.db", check_same_thread=False)
    return SqliteSaver(conn)


@lru_cache
def _agent():
    return create_agent(
        model=make_llm(),
        tools=TOOLS,
        state_schema=TripAgentState,
        middleware=[*default_guards(), _prompt],
        checkpointer=_checkpointer(),
    )


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}, "recursion_limit": AGENT_RECURSION_LIMIT}


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


def _run(session_id: str, payload):
    """Прогон графа как поток событий SSE: reasoning -> (token | block | tool)* -> confirm? -> done."""
    config = _config(session_id)
    yield {"type": "reasoning"}
    for mode, data in _agent().stream(payload, config, stream_mode=["messages", "custom"]):
        if mode == "custom":
            yield data
            continue
        chunk, meta = data
        if isinstance(chunk, ToolMessage):
            yield {"type": "tool", "name": chunk.name}
        elif isinstance(chunk, AIMessageChunk):
            if meta.get("langgraph_node") == "model" and (token := _text(chunk)):
                yield {"type": "token", "text": token}
        elif isinstance(chunk, AIMessage):
            # готовый ответ целиком: отказ фильтра или модель без стриминга
            if text_out := _text(chunk):
                yield {"type": "token", "text": text_out}
    if confirm := pending_checkout(session_id):
        yield confirm


def pending_checkout(session_id: str) -> dict | None:
    """Событие confirm для заказа, который ждёт кнопки, или None: чтобы восстановить карточку после перезагрузки."""
    pending = _pending_checkout(session_id)
    return {**pending.value, "type": "confirm", "action": "checkout"} if pending else None


def _guarded(session_id: str, events):
    """Один запрос на сессию за раз; ошибки не уходят клиенту, только в лог."""
    try:
        with _session_lock(session_id):
            yield from events()
    except Exception:
        log.exception("Агент упал на сессии %s", session_id)
        yield {"type": "error", "detail": FAILED}
        return
    yield {"type": "done"}


def stream_message(session_id: str, text: str):
    """Генератор событий SSE на сообщение пользователя."""

    def events():
        pending = _pending_checkout(session_id)
        if pending is None:
            yield from _run(session_id, {"messages": [{"role": "user", "content": text}]})
            return
        # Пользователь написал, не нажав кнопку оплаты: это отказ от оплаты, а текст уходит
        # модели вместе с отказом. Фильтр проверяем сами: в обход входного middleware.
        message = None if inspect_text(text) else text
        yield from _run(session_id, Command(resume={"approved": False, "message": message}))

    yield from _guarded(session_id, events)


def resume_checkout(session_id: str, approved: bool):
    """Генератор событий SSE на нажатие «Оплатить» или «Отмена»."""

    def events():
        if _pending_checkout(session_id) is None:
            yield {"type": "error", "detail": "Нет заказа, ожидающего подтверждения."}
            return
        yield from _run(session_id, Command(resume={"approved": approved}))

    yield from _guarded(session_id, events)


def collect(session_id: str, events) -> AgentResponse:
    """Собирает поток событий в один ответ (для CLI и синхронного API)."""
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


def handle_message(session_id: str, text: str) -> AgentResponse:
    return collect(session_id, stream_message(session_id, text))
