"""Shared retry helper for Gemini calls that hit free-tier rate limits.

Gemini's free tier returns 429 / RESOURCE_EXHAUSTED under burst traffic.
Retrying with exponential backoff keeps a single provider viable without
paying for a tier bump — the OpenAI failover only kicks in if Gemini is
still unavailable after these retries.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RATE_LIMIT_MARKERS = (
    "429",
    "resource_exhausted",
    "quota",
    "rate limit",
    "too many requests",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


async def call_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float,
    label: str,
) -> T:
    """Await ``factory()``, retrying only on rate-limit errors.

    Non-rate-limit errors propagate immediately. The last error is re-raised
    after retries are exhausted so the caller's existing except-block handles
    it (returns the 'unknown' score shape).
    """
    attempt = 0
    while True:
        try:
            return await factory()
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            attempt += 1
            logger.info(
                "%s rate-limited (attempt %d/%d); retrying in %.1fs",
                label, attempt, max_retries, delay,
            )
            await asyncio.sleep(delay)
