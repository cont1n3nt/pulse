import logging
import threading
from functools import lru_cache

from langchain.agents import create_agent
from langchain.agents.middleware import dynamic_prompt
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from .config import AGENT_RECURSION_LIMIT, make_llm
from .contracts import AgentResponse, Block, FlightOption, TripState
from .prompts import system_prompt
from .tools.flights import TOOLS

log = logging.getLogger(__name__)

_STATES: dict[str, TripState] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_LOCK = threading.Lock()


@dynamic_prompt
def _prompt(request) -> str:
    # Вызывается на каждый запрос, поэтому дата в промпте всегда актуальна.
    return system_prompt()


@lru_cache
def _agent():
    return create_agent(
        model=make_llm(),
        tools=TOOLS,
        middleware=[_prompt],
        checkpointer=InMemorySaver(),
    )


def _session_lock(session_id: str) -> threading.Lock:
    with _LOCK:
        return _SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _text(msg) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def get_state(session_id: str) -> TripState:
    with _LOCK:
        return _STATES.setdefault(session_id, TripState())


def reset_session(session_id: str) -> None:
    with _LOCK:
        _STATES.pop(session_id, None)
        _SESSION_LOCKS.pop(session_id, None)


def handle_message(session_id: str, text: str) -> AgentResponse:
    state = get_state(session_id)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": AGENT_RECURSION_LIMIT}

    try:
        # Один запрос на сессию за раз: параллельные сообщения иначе затрут state друг друга.
        with _session_lock(session_id):
            result = _agent().invoke({"messages": [{"role": "user", "content": text}]}, config)
    except Exception:
        log.exception("Агент упал на сессии %s", session_id)
        return AgentResponse(
            session_id=session_id,
            message="Не получилось обработать запрос, попробуйте ещё раз.",
            blocks=[Block(type="error", data={"detail": "agent_failed"})],
            state=state,
        )

    messages = result["messages"]
    last_human = max(i for i, m in enumerate(messages) if m.type == "human")
    turn = messages[last_human + 1:]

    blocks: list[Block] = []
    tool_calls: list[str] = []
    for m in turn:
        if isinstance(m, AIMessage):
            tool_calls += [c["name"] for c in m.tool_calls or []]
        if isinstance(m, ToolMessage) and m.name == "search_flights" and m.artifact:
            flights = [FlightOption.model_validate(f) for f in m.artifact]
            state.flights = flights
            state.stage = "compare"
            blocks.append(Block(type="flights", data={"items": m.artifact}))

    return AgentResponse(
        session_id=session_id,
        message=_text(messages[-1]),
        blocks=blocks,
        state=state,
        tool_calls=tool_calls,
    )


def stream_message(session_id: str, text: str):
    """Генератор событий для SSE: reasoning -> token* -> done (или error)."""
    state = get_state(session_id)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": AGENT_RECURSION_LIMIT}

    yield {"type": "reasoning"}
    try:
        with _session_lock(session_id):
            for chunk, meta in _agent().stream(
                {"messages": [{"role": "user", "content": text}]}, config, stream_mode="messages"
            ):
                if isinstance(chunk, ToolMessage):
                    if chunk.name == "search_flights" and chunk.artifact:
                        state.flights = [FlightOption.model_validate(f) for f in chunk.artifact]
                        state.stage = "compare"
                elif isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "model":
                    token = _text(chunk)
                    if token:
                        yield {"type": "token", "text": token}
    except Exception:
        log.exception("Агент упал на сессии %s", session_id)
        yield {"type": "error", "detail": "Не получилось обработать запрос, попробуйте ещё раз."}
        return
    yield {"type": "done"}
