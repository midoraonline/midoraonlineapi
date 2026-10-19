"""Shared guest ranking order.

In-process memory is an L1 cache (fastest, but wiped on every restart/deploy).
Redis is the L2 cache — survives restarts and is shared if this ever runs as
more than one process. Falls back to the `feed_rank_snapshots` Postgres table
only when Redis isn't configured (see core/cache.py).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.cache import cache_delete, cache_get_json, cache_set_json, get_redis

logger = logging.getLogger(__name__)

GUEST_KEY = "guest"
_REDIS_KEY = "feed:guest:ranked_ids"
TTL_SECONDS = 60.0

_lock = threading.Lock()
_memory: tuple[float, list[str]] | None = None


def clear_memory() -> None:
    global _memory
    with _lock:
        _memory = None


def memory_ids() -> list[str] | None:
    with _lock:
        if _memory is None:
            return None
        stored_at, ids = _memory
        if time.monotonic() - stored_at > TTL_SECONDS:
            return None
        return list(ids)


def remember(ids: list[str]) -> None:
    global _memory
    with _lock:
        _memory = (time.monotonic(), list(ids))


def load_fresh(client: Any) -> list[str] | None:
    if get_redis() is not None:
        ids = cache_get_json(_REDIS_KEY)
        if isinstance(ids, list) and ids:
            return [str(pid) for pid in ids if pid]
        return None
    return _load_fresh_from_postgres(client)


def _load_fresh_from_postgres(client: Any) -> list[str] | None:
    try:
        row = (
            client.table("feed_rank_snapshots")
            .select("ranked_ids,refreshed_at")
            .eq("snapshot_key", GUEST_KEY)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.info("feed:guest snapshot read skipped: %s", exc)
        return None
    if not row.data:
        return None
    item = row.data[0] or {}
    if not _cache_is_fresh_seconds(item.get("refreshed_at"), TTL_SECONDS):
        return None
    raw = item.get("ranked_ids") or []
    if not isinstance(raw, list):
        return None
    return [str(pid) for pid in raw if pid]


def _cache_is_fresh_seconds(raw: Any, ttl: float) -> bool:
    from datetime import datetime, timezone

    if not raw:
        return False
    try:
        text = str(raw)
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return 0 <= age <= ttl


def save(client: Any, ids: list[str]) -> None:
    if get_redis() is not None:
        cache_set_json(_REDIS_KEY, ids[:800], ttl_seconds=int(TTL_SECONDS))
        return
    _save_to_postgres(client, ids)


def _save_to_postgres(client: Any, ids: list[str]) -> None:
    from datetime import datetime, timezone

    try:
        client.table("feed_rank_snapshots").upsert(
            {
                "snapshot_key": GUEST_KEY,
                "ranked_ids": ids[:800],
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="snapshot_key",
        ).execute()
    except Exception as exc:
        logger.info("feed:guest snapshot write skipped: %s", exc)


def invalidate(client: Any | None = None) -> None:
    clear_memory()
    if get_redis() is not None:
        cache_delete(_REDIS_KEY)
        return
    if client is None:
        from db.supabase import get_supabase_admin

        client = get_supabase_admin()
    try:
        client.table("feed_rank_snapshots").delete().eq("snapshot_key", GUEST_KEY).execute()
    except Exception as exc:
        logger.info("feed:guest snapshot invalidate skipped: %s", exc)

