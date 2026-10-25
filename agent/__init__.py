"""Публичный интерфейс агента. Бэк импортирует ТОЛЬКО отсюда:

    from agent import handle_message, get_state, AgentResponse, ChatRequest, TripState
"""

from .contracts import AgentResponse, Block, ChatRequest, TripState
from .service import get_state, handle_message, reset_session

__all__ = ["handle_message", "get_state", "reset_session",
           "AgentResponse", "Block", "ChatRequest", "TripState"]
