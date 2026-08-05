"""Tests for app.llm's hand-rolled DeepSeek chat client."""

import json

import httpx
import pytest

from app.config import settings
from app.llm import LlmError, LlmReply, chat


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_chat_returns_text_and_token_counts(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-v4-flash")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })

    reply = await chat("sys", "usr", transport=_transport(handler))
    assert reply == LlmReply(text="hello", tokens_in=12, tokens_out=3)
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "deepseek-v4-flash"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


async def test_missing_key_or_model_raises_before_any_network(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    with pytest.raises(LlmError):
        await chat("s", "u", transport=_transport(lambda r: httpx.Response(500)))


async def test_http_error_and_bad_body_raise_llm_error(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")
    with pytest.raises(LlmError):
        await chat("s", "u", transport=_transport(lambda r: httpx.Response(429)))
    with pytest.raises(LlmError):
        await chat("s", "u", transport=_transport(
            lambda r: httpx.Response(200, json={"choices": []})))


async def test_malformed_json_body_raises_llm_error(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")
    with pytest.raises(LlmError):
        await chat("s", "u", transport=_transport(
            lambda r: httpx.Response(200, text="not json")))
