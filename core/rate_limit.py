"""In-process sliding-window rate limiter.

Per-instance only (uvicorn worker / serverless isolate). That is enough to
stop credential stuffing and ingest floods on a single node; a shared store
would be the next step if we run many replicas.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from time import monotonic
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

_hits: dict[str, deque[float]] = defaultdict(deque)
_MAX_KEYS = 20_000


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64] or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(bucket: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 when `bucket` has exceeded `limit` hits in `window_seconds`."""
    now = monotonic()
    cutoff = now - window_seconds
    q = _hits[bucket]
    while q and q[0] <= cutoff:
        q.popleft()
    if len(q) >= limit:
        retry = max(1, int(window_seconds - (now - q[0])))
        logger.warning("rate limit exceeded bucket=%s retry=%s", bucket.split(":")[0], retry)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(retry)},
        )
    q.append(now)
    if len(_hits) > _MAX_KEYS:
        stale = [key for key, hits in _hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale[: _MAX_KEYS // 2]:
            _hits.pop(key, None)


def rate_limit(
    *,
    limit: int,
    window_seconds: int,
    bucket: str,
) -> Callable[..., None]:
    """FastAPI dependency: limit by client IP for a named bucket."""

    def dependency(request: Request) -> None:
        check_rate_limit(
            f"{bucket}:ip:{client_ip(request)}",
            limit=limit,
            window_seconds=window_seconds,
        )

    return dependency


RateLimitLogin = Annotated[None, Depends(rate_limit(limit=10, window_seconds=60, bucket="auth.login"))]
RateLimitRegister = Annotated[None, Depends(rate_limit(limit=5, window_seconds=60, bucket="auth.register"))]
RateLimitRefresh = Annotated[None, Depends(rate_limit(limit=30, window_seconds=60, bucket="auth.refresh"))]
RateLimitOtp = Annotated[None, Depends(rate_limit(limit=5, window_seconds=60, bucket="auth.otp"))]
RateLimitIngest = Annotated[None, Depends(rate_limit(limit=60, window_seconds=60, bucket="analytics.ingest"))]
RateLimitAi = Annotated[None, Depends(rate_limit(limit=20, window_seconds=60, bucket="ai.public"))]
RateLimitGoogle = Annotated[None, Depends(rate_limit(limit=20, window_seconds=60, bucket="auth.google"))]
