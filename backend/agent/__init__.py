"""Публичный интерфейс агента. Бэк импортирует ТОЛЬКО отсюда:

    from backend.agent import handle_message, get_state, AgentResponse, ChatRequest, TripState
"""

from .contracts import AgentResponse, Block, ChatRequest, TripState
from .service import get_state, handle_message, reset_session, stream_message

__all__ = ["handle_message", "stream_message", "get_state", "reset_session",
           "AgentResponse", "Block", "ChatRequest", "TripState"]
