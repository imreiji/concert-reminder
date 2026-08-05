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
    transport error, non-200 status, a body missing the expected
    `choices[0].message.content` shape, a non-`"stop"` `finish_reason`
    (the reply was truncated), or empty `content`. Thinking mode is
    disabled in the request body (see `body` below) precisely so a
    reasoning overrun cannot produce the truncated/empty case in the first
    place; the check stays as a second line of defense.

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
        # deepseek-v4-flash thinks by default, and thinking burns ~50k
        # reasoning tokens on a 216-lead classify call -- ~90% of the call's
        # cost -- with no visible output until it finishes. On 2026-08-05 a
        # classify run's reasoning overran the output cap and came back with
        # `content` empty, which is what produced the production
        # TriageResponseError. `temperature` also has NO effect while
        # thinking is enabled, per DeepSeek's docs, so the 0.1 above was
        # inert until this was added. Unconditional: this client serves only
        # the triage rubric calls, so there is no case where thinking helps.
        "thinking": {"type": "disabled"},
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

    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # response.json() raises json.JSONDecodeError (a ValueError) on a
        # non-JSON or truncated 200 body; folding it in here keeps every
        # post-request failure surfacing as LlmError, per the docstring.
        raise LlmError("DeepSeek response missing choices[0].message.content") from exc

    # The OpenAI-compatible schema permits `"content": null`; normalize it to
    # "" so it lands in the empty-reply check below instead of raising
    # AttributeError out of `.strip()`.
    content = content or ""

    # A capped reply comes back with a finish_reason other than "stop" (e.g.
    # "length"), and 2026-08-05's incident showed that can leave `content`
    # empty or partial -- which used to fail later and further away, as an
    # opaque TriageResponseError out of the YAML parser. Check truncation
    # first: an empty reply that was also truncated should report the more
    # informative cause. A missing finish_reason key is accepted as-is
    # (nothing to complain about).
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and finish_reason != "stop":
        raise LlmError(f"DeepSeek reply truncated (finish_reason: {finish_reason})")
    if not content.strip():
        raise LlmError("DeepSeek reply was empty")

    usage = payload.get("usage") or {}
    return LlmReply(
        text=content,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
    )
