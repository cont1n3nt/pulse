"""Понятные причины сбоя модели и короткие названия чатов: без сети и модели."""

import httpx
import openai

from backend.agent import service
from backend.endpoints import short_title


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://llm.test/v1/chat/completions"))


def test_missing_key_is_named():
    assert "LLM_API_KEY" in service._explain(service.ModelNotConfigured("нет ключа"))


def test_provider_error_is_found_under_middleware_wrapping():
    try:
        try:
            raise openai.AuthenticationError("bad key", response=_response(401), body=None)
        except openai.AuthenticationError as exc:
            raise RuntimeError("model call failed") from exc
    except RuntimeError as wrapped:
        assert "не принял ключ" in service._explain(wrapped)


def test_rate_limit_and_unknown_errors():
    assert "ограничил" in service._explain(openai.RateLimitError("429", response=_response(429), body=None))
    assert service._explain(ValueError("что-то в инструменте")) == service.FAILED


def test_short_title_keeps_whole_words():
    assert short_title("  Сочи   в мае ") == "Сочи в мае"
    title = short_title("Из Москвы в Сочи на майские, двое с ребёнком 6 лет, до 120 тысяч")
    assert title == "Из Москвы в Сочи на майские…"
    assert len(title) <= 33
    assert short_title("Оченьдлинноесловобезпробеловкотороененапишетчеловек").endswith("…")
