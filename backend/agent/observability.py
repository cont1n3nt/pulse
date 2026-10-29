"""Журнал хода агента: каждый вызов модели и инструмента — строка в логе со временем и итогом.

Стоит внутри повторов и запасных моделей, поэтому каждая попытка видна отдельно.
Подробная трассировка с промптами и ответами — в LangSmith, здесь только то, что нужно для отладки.
"""

import json
import logging
import time

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

log = logging.getLogger(__name__)

ARGS_LIMIT = 300


def _model_name(model) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__


def _short(args) -> str:
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) <= ARGS_LIMIT else text[:ARGS_LIMIT] + "…"


def _ai_message(response) -> AIMessage | None:
    if isinstance(response, AIMessage):
        return response
    return next((m for m in reversed(getattr(response, "result", [])) if isinstance(m, AIMessage)), None)


class LoggingMiddleware(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        name = _model_name(request.model)
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception as exc:
            log.warning("model %s: ошибка %s за %.1f с", name, type(exc).__name__, time.perf_counter() - started)
            raise
        message = _ai_message(response)
        calls = [c["name"] for c in message.tool_calls] if message else []
        usage = (message.usage_metadata or {}) if message else {}
        log.info("model %s: %.1f с, токены %s→%s, %s", name, time.perf_counter() - started,
                 usage.get("input_tokens", "?"), usage.get("output_tokens", "?"),
                 f"вызывает {', '.join(calls)}" if calls else "ответ текстом")
        return response

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        started = time.perf_counter()
        try:
            result = handler(request)
        except GraphInterrupt:
            log.info("tool %s: ждёт подтверждения пользователя", call["name"])
            raise
        except Exception as exc:
            log.warning("tool %s: ошибка %s за %.1f с, args=%s", call["name"], type(exc).__name__,
                        time.perf_counter() - started, _short(call.get("args")))
            raise
        failed = isinstance(result, ToolMessage) and result.status == "error"
        log.log(logging.WARNING if failed else logging.INFO, "tool %s: %s за %.1f с, args=%s", call["name"],
                "ошибка" if failed else "ok", time.perf_counter() - started, _short(call.get("args")))
        return result
