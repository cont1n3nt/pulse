"""Публичный интерфейс агента. Бэк импортирует ТОЛЬКО отсюда:

    from backend.agent import stream_message, resume_checkout, pending_checkout, get_state, get_order
"""

from .contracts import AgentResponse, Block, ChatRequest, Order, TripState
from .orders import get as get_order
from .service import (get_state, handle_message, pending_checkout, reset_session, resume_checkout,
                      stream_message)

__all__ = ["handle_message", "stream_message", "resume_checkout", "pending_checkout", "get_state",
           "get_order", "reset_session", "AgentResponse", "Block", "ChatRequest", "Order", "TripState"]
