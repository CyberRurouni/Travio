"""
API-error handling — entirely separate from truncation handling.

Truncation ("the response wasn't finished") is handled inside each case
in client.py, via max_continuations or max_expansions. It is NOT an error
in the sense this file cares about.

This file only deals with the API call itself failing — a bad key, a rate
limit, a dropped connection, a server having a bad moment. Some of those
are worth retrying, some aren't, and the ones worth retrying don't all
deserve the same wait. ApiErrorHandler is the single place that decides.
"""

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger("AI UTILS")

# Some errors are permanent no matter how many times you retry — a bad API
# key, a request the model rejects outright, a model name that doesn't
# exist. Retrying those just wastes time before failing anyway, so we fail
# fast instead of spending any retry budget on them.
# Others are transient — rate limits, timeouts, a model server having a bad
# moment — and genuinely can succeed on a retry.
try:
    import openai as _openai_sdk

    _NON_RETRYABLE_ERRORS = (
        _openai_sdk.AuthenticationError,    # bad/missing API key
        _openai_sdk.PermissionDeniedError,  # not allowed to use this model/resource
        _openai_sdk.NotFoundError,          # model or resource doesn't exist
        _openai_sdk.BadRequestError,        # malformed request — retrying won't fix it
        _openai_sdk.UnprocessableEntityError,
    )
except ImportError:
    _NON_RETRYABLE_ERRORS = ()  # can't classify without the SDK's error types — default to retrying


def is_retryable(error: Exception) -> bool:
    """True if this error is worth retrying at all; False if it will just fail the same way again."""
    return not isinstance(error, _NON_RETRYABLE_ERRORS)


def _extract_retry_after(error: Exception) -> Optional[float]:
    """
    Pull a Retry-After hint straight from the API's response, if it gave one.
    This is authoritative — if the API tells us exactly how long to wait,
    we respect that instead of guessing with backoff.
    """
    response = getattr(error, "response", None)
    if response is None:
        return None

    headers = getattr(response, "headers", None)
    if not headers:
        return None

    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ApiErrorHandler:
    """
    Owns the retry budget and wait logic for genuine API-call failures —
    entirely separate from a case method's own truncation-handling logic.

    Usage, inside a case method's call loop:

        error_handler = ApiErrorHandler(max_retries=max_retries)
        while True:
            try:
                resp = await asyncio.to_thread(client.chat.completions.create, ...)
            except Exception as e:
                await error_handler.handle(e)   # sleeps, or raises if unrecoverable
                continue                         # if we get here, try again
            ...

    handle() either:
      - raises immediately (non-retryable error, or retry budget exhausted), or
      - waits an appropriate amount of time and returns, signaling "try again"

    Wait logic: if the API gave a Retry-After hint, that's respected exactly.
    Otherwise, exponential backoff with a little jitter (so many concurrent
    calls retrying at once don't all hammer the API in lockstep), capped at
    a sane maximum.
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.attempts_used = 0

    async def handle(self, error: Exception) -> None:
        self.attempts_used += 1

        if not is_retryable(error):
            logger.error(f"🚫 Non-retryable API error — failing immediately: {error}")
            raise RuntimeError(f"Non-retryable API error: {error}") from error

        if self.attempts_used > self.max_retries:
            logger.error(
                f"🚨 API error retry budget exhausted ({self.max_retries} retries): {error}"
            )
            raise RuntimeError(f"API error, retries exhausted: {error}") from error

        delay = self._compute_delay(error)
        logger.warning(
            f"💥 API error (retry {self.attempts_used}/{self.max_retries}) "
            f"— waiting {delay:.1f}s before trying again: {error}"
        )
        await asyncio.sleep(delay)

    def _compute_delay(self, error: Exception) -> float:
        retry_after = _extract_retry_after(error)
        if retry_after is not None:
            return retry_after

        # Exponential backoff with jitter, capped
        exp = min(self.base_delay * (2 ** (self.attempts_used - 1)), self.max_delay)
        jitter = random.uniform(0, exp * 0.25)
        return exp + jitter