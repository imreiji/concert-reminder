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
    assert seen["body"]["thinking"] == {"type": "disabled"}
    # Sent EXPLICITLY, never inherited: DeepSeek's own default is 8,192, and on
    # 2026-08-09 an unbatched classify reply hit it exactly and raised. A
    # ceiling a third party picked is not a ceiling this app chose.
    assert seen["body"]["max_tokens"] == settings.deepseek_max_tokens


async def test_max_tokens_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")
    monkeypatch.setattr(settings, "deepseek_max_tokens", 1234)
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        })

    await chat("s", "u", transport=_transport(handler))
    assert seen["body"]["max_tokens"] == 1234


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


async def test_truncated_reply_raises_llm_error_naming_finish_reason(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {"content": "partial"},
                "finish_reason": "length",
            }],
        })

    with pytest.raises(LlmError, match="length"):
        await chat("s", "u", transport=_transport(handler))


async def test_empty_reply_raises_llm_error(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {"content": ""},
                "finish_reason": "stop",
            }],
        })

    with pytest.raises(LlmError, match="empty"):
        await chat("s", "u", transport=_transport(handler))


async def test_null_content_raises_llm_error_not_attributeerror(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(settings, "deepseek_model", "m")

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {"content": None},
                "finish_reason": "stop",
            }],
        })

    with pytest.raises(LlmError, match="empty"):
        await chat("s", "u", transport=_transport(handler))
