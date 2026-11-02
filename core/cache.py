"""Shared Redis cache.

Every helper is best-effort: with REDIS_URL unset or Redis down, reads return
None and writes are skipped, so callers fall back to in-process memory or
Postgres. A cache outage must never break a request.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)

PREFIX = "midora:"
_RETRY_AFTER_S = 30.0

_lock = threading.Lock()
_client: Any = None
_down_until = 0.0


def get_redis() -> Any | None:
    """Process-wide client, or None when unconfigured or recently unreachable."""
    global _client, _down_until
    if _client is not None:
        return _client
    if time.monotonic() < _down_until:
        return None
    with _lock:
        if _client is not None:
            return _client
        url = (get_settings().redis_url or "").strip()
        if not url:
            _down_until = float("inf")
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
                decode_responses=True,
            )
            client.ping()
            _client = client
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis unavailable, using fallbacks: %s", exc)
            _down_until = time.monotonic() + _RETRY_AFTER_S
            return None
        return _client


def _failed(op: str, key: str, exc: Exception) -> None:
    global _client, _down_until
    logger.info("redis %s(%s) failed: %s", op, key, exc)
    try:
        import redis

        if isinstance(exc, (redis.ConnectionError, redis.TimeoutError)):
            _client = None
            _down_until = time.monotonic() + _RETRY_AFTER_S
    except Exception:  # noqa: BLE001
        pass


def reset_redis() -> None:
    global _client, _down_until
    with _lock:
        _client = None
        _down_until = 0.0


def key(*parts: object) -> str:
    return PREFIX + ":".join(str(p) for p in parts)


def get_json(name: str) -> Any | None:
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(name)
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        _failed("get", name, exc)
        return None


def set_json(name: str, value: Any, *, ttl_seconds: float) -> bool:
    r = get_redis()
    if r is None:
        return False
    try:
        r.set(name, json.dumps(value, default=str), ex=max(1, int(ttl_seconds)))
        return True
    except Exception as exc:  # noqa: BLE001
        _failed("set", name, exc)
        return False


def delete(*names: str) -> None:
    r = get_redis()
    if r is None or not names:
        return
    try:
        r.delete(*names)
    except Exception as exc:  # noqa: BLE001
        _failed("delete", names[0], exc)


def acquire_lock(name: str, *, ttl_seconds: float) -> bool:
    """True when this process got the lock, or when Redis is unavailable (no coordination)."""
    r = get_redis()
    if r is None:
        return True
    try:
        return bool(r.set(name, "1", nx=True, ex=max(1, int(ttl_seconds))))
    except Exception as exc:  # noqa: BLE001
        _failed("lock", name, exc)
        return True


def release_lock(name: str) -> None:
    delete(name)


def generation(name: str) -> int:
    """Monotonic counter used to version cache keys; bump() invalidates them all at once."""
    r = get_redis()
    if r is None:
        return 0
    try:
        return int(r.get(name) or 0)
    except Exception as exc:  # noqa: BLE001
        _failed("get", name, exc)
        return 0


def bump(name: str) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.incr(name)
    except Exception as exc:  # noqa: BLE001
        _failed("incr", name, exc)


def hit_window(name: str, *, window_seconds: int) -> tuple[int, int] | None:
    """Fixed-window counter: (hits in window, seconds left). None when Redis is unavailable."""
    r = get_redis()
    if r is None:
        return None
    try:
        hits = int(r.incr(name))
        if hits == 1:
            r.expire(name, window_seconds)
        ttl = r.ttl(name)
        if ttl is None or ttl < 0:
            r.expire(name, window_seconds)
            ttl = window_seconds
        return hits, max(1, int(ttl))
    except Exception as exc:  # noqa: BLE001
        _failed("incr", name, exc)
        return None
