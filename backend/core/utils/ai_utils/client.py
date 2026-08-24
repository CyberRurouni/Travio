"""
AIClient — the public surface of this package.

Two ways to get JSON back from a model:
  - stream():   opens a streaming connection, stops the instant the JSON
                balances. If it gets cut off, asks the model to CONTINUE
                from the partial output (max_continuations rounds).
  - blocking(): one plain blocking call. If it gets cut off, REGENERATES
                the whole response from scratch with a bigger token
                ceiling (max_expansions rounds) — no partial output is
                carried forward, since a blocking call doesn't have a
                live signal to know when to stop early anyway.

Both are always JSON-in, JSON-out. There is no text mode and no
response_format param — if you need raw text, that's a different tool.

Truncation handling (max_continuations / max_expansions) lives entirely
inside these two methods — it's not a "failure," it's "not done yet."
Genuine API-call failures (bad key, rate limit, dropped connection) are
handled by errors.ApiErrorHandler, on its own separate max_retries budget,
outside of and unrelated to the truncation counters.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from .streaming import JsonStreamReader, build_continuation_prompt
from .parsing import extract_balanced_json, looks_truncated
from .errors import ApiErrorHandler

logger = logging.getLogger("AI UTILS")

DEFAULT_MODEL = "google/gemini-2.5-flash"


class AIClient:
    """
    Usage:
        ai = AIClient()
        result = await ai.stream(messages=[...])
        # or
        result = await ai.blocking(messages=[...])

    Pass client= to use a specific already-constructed OpenAI-compatible
    client. If omitted, lazily imports `core.client` on first use (same
    lazy-import pattern the old single-file version used, to avoid a
    circular import).
    """

    def __init__(self, model: str = DEFAULT_MODEL, client: Optional[Any] = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from core import client  # Lazy import to avoid circular dependency
            self._client = client
        return self._client

    @staticmethod
    def _grow_budget(current_tokens: int, increment: int, step_number: int) -> int:
        """
        Shared token-budget growth formula, used by both stream() (per
        continuation round) and blocking() (per expansion round).
        Grows faster on repeated failures: step 1 → +increment,
        step 2 → +2*increment, step 3 → +3*increment, etc.
        """
        return current_tokens + (increment * step_number)

    # ------------------------------------------------------------------
    # Stream Case
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages,
        model: Optional[str] = None,
        max_tokens: int = 800,
        increment: int = 200,
        max_continuations: int = 3,
        temperature: float = 0.0,
        max_retries: int = 3,
        fallback: Optional[Dict[str, Any]] = None,
    ) -> Optional[dict]:
        """
        Stream a JSON response, stopping the instant it balances. If cut
        off, asks the model to continue from the partial output — up to
        max_continuations times. No separate "retry" concept for
        truncation; being cut off just means "not done yet, keep going."

        Genuine API errors (not truncation) are handled by ApiErrorHandler
        on its own max_retries budget — entirely separate from continuations.

        Raises on unrecoverable failure, unless `fallback` is given, in
        which case that's returned instead.
        """
        try:
            return await self._stream_impl(
                messages=messages,
                model=model or self.model,
                max_tokens=max_tokens,
                increment=increment,
                max_continuations=max_continuations,
                temperature=temperature,
                max_retries=max_retries,
            )
        except Exception as e:
            if fallback is not None:
                logger.error(f"🚨 stream() failed — returning fallback. Error: {e}")
                return fallback
            raise

    async def _stream_impl(
        self, messages, model, max_tokens, increment, max_continuations, temperature, max_retries,
    ) -> dict:
        error_handler = ApiErrorHandler(max_retries=max_retries)
        reader = JsonStreamReader()
        continuation_messages = list(messages)
        continuations_used = 0
        blob = None

        while True:
            try:
                blob = await asyncio.to_thread(
                    reader.read,
                    self.client,
                    is_continuation=(continuations_used > 0),
                    model=model,
                    messages=continuation_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                await error_handler.handle(e)  # sleeps and returns, or raises
                continue  # try the same request again after the wait

            if blob is not None:
                break
            if continuations_used >= max_continuations:
                break

            continuations_used += 1
            max_tokens = self._grow_budget(max_tokens, increment, continuations_used)
            logger.info(
                f"↪️  Response cut off — continuation {continuations_used}/{max_continuations} "
                f"| new max_tokens={max_tokens} | {len(reader.buffer)} chars collected so far"
            )
            # Ends on a USER turn on purpose — see build_continuation_prompt.
            continuation_messages = list(messages) + [
                {"role": "assistant", "content": reader.buffer},
                {"role": "user", "content": build_continuation_prompt(reader)},
            ]

        if not reader.buffer or not reader.buffer.strip():
            raise ValueError("Model returned an empty response")

        if blob is None:
            raise ValueError(
                f"JSON never balanced after {continuations_used} continuation(s) "
                f"— collected {len(reader.buffer)} chars: {reader.buffer[-100:]!r}"
            )

        try:
            return json.loads(blob)
        except json.JSONDecodeError as e:
            # Balanced brace-wise but not valid JSON — rare, and not a
            # truncation signal, so out of scope for continuation. Fails
            # immediately rather than guessing at a fix.
            raise ValueError(f"Stream produced a balanced but invalid JSON span: {e}") from e

    # ------------------------------------------------------------------
    # Non-Stream (blocking) Case
    # ------------------------------------------------------------------

    async def blocking(
        self,
        messages,
        model: Optional[str] = None,
        max_tokens: int = 800,
        increment: int = 200,
        max_expansions: int = 3,
        temperature: float = 0.0,
        max_retries: int = 3,
        fallback: Optional[Dict[str, Any]] = None,
    ) -> Optional[dict]:
        """
        One plain blocking call for a JSON response. If the response looks
        truncated (heuristic — no live signal here, unlike stream()), the
        ENTIRE request is reissued with a bigger token ceiling — up to
        max_expansions times. No partial output is carried forward; each
        expansion is a full regeneration from scratch.

        Genuine API errors (not truncation) are handled by ApiErrorHandler
        on its own max_retries budget — entirely separate from expansions.

        Raises on unrecoverable failure, unless `fallback` is given, in
        which case that's returned instead.
        """
        try:
            return await self._blocking_impl(
                messages=messages,
                model=model or self.model,
                max_tokens=max_tokens,
                increment=increment,
                max_expansions=max_expansions,
                temperature=temperature,
                max_retries=max_retries,
            )
        except Exception as e:
            if fallback is not None:
                logger.error(f"🚨 blocking() failed — returning fallback. Error: {e}")
                return fallback
            raise

    async def _blocking_impl(
        self, messages, model, max_tokens, increment, max_expansions, temperature, max_retries,
    ) -> dict:
        error_handler = ApiErrorHandler(max_retries=max_retries)
        expansions_used = 0

        while True:
            try:
                resp = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                await error_handler.handle(e)
                continue

            text = resp.choices[0].message.content
            text = text.strip() if text else ""

            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass

                blob = extract_balanced_json(text)
                if blob:
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError:
                        pass

            # Nothing parsed. Empty response or a heuristically-truncated
            # one both get treated as "needs a bigger ceiling, try again."
            if not text or looks_truncated(text):
                if expansions_used >= max_expansions:
                    raise ValueError(
                        f"Response still truncated after {expansions_used} expansion(s): "
                        f"{text[-100:]!r}"
                    )
                expansions_used += 1
                max_tokens = self._grow_budget(max_tokens, increment, expansions_used)
                logger.warning(
                    f"🔪 Response looks truncated — expansion {expansions_used}/{max_expansions} "
                    f"| new max_tokens={max_tokens}"
                )
                continue

            # Parseable-looking text that still isn't valid JSON, and doesn't
            # look truncated — a genuine content problem, not a budget one.
            # Out of scope for max_expansions; fails immediately.
            preview = text[:200].replace("\n", " ")
            raise ValueError(f"Invalid JSON from model (not a truncation issue): {preview!r}")