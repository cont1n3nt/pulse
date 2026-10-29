"""Публичный интерфейс агента. Бэк импортирует ТОЛЬКО отсюда:

    from backend.agent import stream_message, resume_checkout, pending_checkout, get_state, get_order
"""

from .contracts import AgentResponse, Block, Order, TripState
from .guardrails import MAX_INPUT_CHARS
from .providers import orders as _orders
from .service import (account_cart, change_account_cart, forget_memory, get_memory, get_state, handle_message, hotel_photo, pending_checkout,
                      reset_session, resume_checkout, stream_message, trip_view)


def get_order(order_id: str) -> Order | None:
    return _orders().get(order_id)


__all__ = ["handle_message", "stream_message", "resume_checkout", "pending_checkout", "get_state",
           "get_order", "reset_session", "get_memory", "forget_memory", "hotel_photo", "trip_view",
           "account_cart", "change_account_cart", "MAX_INPUT_CHARS", "AgentResponse", "Block", "Order", "TripState"]
