"""One hand-rolled DeepSeek chat call, shared by the AI-triage runner.

Top-level, beside `fetching.py` and `i18n.py`: it does I/O (an HTTP POST), so
it cannot live in `domain/`. It is not part of `triage.py` either -- that
module is the triage PROMPT and the shape of a triage decision; this one is
provider plumbing (auth header, endpoint, request/response shape) with no
opinion about what the messages say, the same separation `ics_read.py` draws
from `discovery.py`.

No OpenAI-compatible SDK dependency, on purpose: the whole surface this app
needs is one JSON POST to `/chat/completions` and one JSON response to read
back, which is exactly the trade-off `domain/ics_read.py` made against a
calendar library -- a hand-rolled twenty-line client is cheaper to audit and
keep in sync with the one endpoint this app calls than a general-purpose SDK
whose surface (streaming, function calling, retries, other providers' quirks)
this app does not use.
"""

from dataclasses import dataclass

import httpx

from app.config import settings

LLM_TIMEOUT_SECONDS = 120.0


class LlmError(Exception):
    """Anything that stopped `chat()` from returning a reply."""


@dataclass(frozen=True)
class LlmReply:
    """A completed chat turn: the text plus the token counts DeepSeek billed."""

    text: str
    tokens_in: int
    tokens_out: int


async def chat(
    system: str,
    user: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = LLM_TIMEOUT_SECONDS,
) -> LlmReply:
    """Send one system+user turn to DeepSeek and return the reply.

    Raises `LlmError` before any network call when the API key or model is
    unset (misconfiguration named plainly), and after the call on any
    transport error, non-200 status, or a body missing the expected
    `choices[0].message.content` shape.

    `transport` is test-only (httpx.MockTransport); production always uses
    httpx's default. One client is built per call, mirroring
    `fetching.fetch_html`.
    """
    if not settings.deepseek_api_key or not settings.deepseek_model:
        raise LlmError(
            "DeepSeek is not configured: deepseek_api_key and deepseek_model "
            "must both be set"
        )

    body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}"}

    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                f"{settings.deepseek_base_url}/chat/completions",
                json=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise LlmError(f"DeepSeek request failed: {exc}") from exc

    if response.status_code != 200:
        raise LlmError(f"DeepSeek returned HTTP {response.status_code}")

    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError("DeepSeek response missing choices[0].message.content") from exc

    usage = payload.get("usage") or {}
    return LlmReply(
        text=content,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
    )
