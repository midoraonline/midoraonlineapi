"""Shared Redis client for cross-process caching.

Replaces two Vercel-era workarounds that don't fit a persistent server:
  * in-process TTL dicts (feed/snapshots.py, etc.) — wiped on every restart/deploy
  * Postgres-table-backed caches (feed_rank_snapshots, user_feed_cache) — a real
    network round trip just to avoid losing state between serverless invocations

Redis gives native TTL + survives restarts + is far cheaper per call than a
Postgres round trip. Every helper here is best-effort: if REDIS_URL is unset
or the instance is unreachable, callers get None/empty and fall back to
whatever they did before (in-process cache or a fresh Supabase query) — a
cache being unavailable must never break a request.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None
_unavailable = False


def get_redis():
    """Process-wide Redis client, or None if unconfigured/unreachable."""
    global _client, _unavailable
    if _client is not None or _unavailable:
        return _client
    with _lock:
        if _client is not None or _unavailable:
            return _client
        url = (get_settings().redis_url or "").strip()
        if not url:
            _unavailable = True
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=False,
                health_check_interval=30,
            )
            client.ping()
            _client = client
        except Exception as exc:
            logger.warning("Redis unavailable, caching falls back to defaults: %s", exc)
            _unavailable = True
            return None
        return _client


def reset_redis() -> None:
    """Drop the cached client (tests / after a connection error)."""
    global _client, _unavailable
    with _lock:
        _client = None
        _unavailable = False


def cache_get_json(key: str) -> Any | None:
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.info("cache_get_json(%s) failed: %s", key, exc)
        return None


def cache_set_json(key: str, value: Any, *, ttl_seconds: int) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.set(key, json.dumps(value), ex=max(1, int(ttl_seconds)))
    except Exception as exc:
        logger.info("cache_set_json(%s) failed: %s", key, exc)


def cache_delete(*keys: str) -> None:
    r = get_redis()
    if r is None or not keys:
        return
    try:
        r.delete(*keys)
    except Exception as exc:
        logger.info("cache_delete(%s) failed: %s", keys, exc)


def trending_bump(board: str, member_id: str, *, amount: float = 1.0) -> None:
    """Increment a member's score on a trending sorted set (e.g. 'trending:products')."""
    r = get_redis()
    if r is None or not member_id:
        return
    try:
        r.zincrby(board, amount, member_id)
    except Exception as exc:
        logger.info("trending_bump(%s, %s) failed: %s", board, member_id, exc)


def trending_top(board: str, limit: int = 20) -> list[tuple[str, float]]:
    """Top-scoring members (id, score), highest first. Empty if Redis is unavailable."""
    r = get_redis()
    if r is None:
        return []
    try:
        rows = r.zrevrange(board, 0, max(0, limit - 1), withscores=True)
        return [(member.decode() if isinstance(member, bytes) else member, score) for member, score in rows]
    except Exception as exc:
        logger.info("trending_top(%s) failed: %s", board, exc)
        return []
