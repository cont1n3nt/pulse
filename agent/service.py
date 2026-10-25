import threading
from functools import lru_cache

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from .config import make_llm
from .contracts import AgentResponse, Block, FlightOption, TripState
from .prompts import system_prompt
from .tools.flights import TOOLS

_STATES: dict[str, TripState] = {}
_LOCK = threading.Lock()


@lru_cache
def _agent():
    return create_agent(
        model=make_llm(),
        tools=TOOLS,
        system_prompt=system_prompt(),
        checkpointer=InMemorySaver(),
    )


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


def handle_message(session_id: str, text: str) -> AgentResponse:
    state = get_state(session_id)
    config = {"configurable": {"thread_id": session_id}}

    try:
        result = _agent().invoke({"messages": [{"role": "user", "content": text}]}, config)
    except Exception as exc:
        return AgentResponse(
            session_id=session_id,
            message="Не получилось обработать запрос, попробуйте ещё раз.",
            blocks=[Block(type="error", data={"detail": f"{type(exc).__name__}: {exc}"})],
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
