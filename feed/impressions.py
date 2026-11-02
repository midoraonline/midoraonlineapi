from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.supabase import get_supabase_admin
from feed.signals import chunks

logger = logging.getLogger(__name__)

POOLS = frozenset({"organic", "boosted", "sponsored", "exploration"})
COOLDOWN_MINUTES = 10


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def record_impressions(
    items: Iterable[dict[str, Any]],
    *,
    buyer_id: str | None,
    session_id: str | None,
    device_hash: str | None,
) -> int:
    admin = get_supabase_admin()
    seen: dict[str, dict[str, Any]] = {}
    for item in items or []:
        listing_id = str(item.get("listing_id") or "").strip()
        if not listing_id:
            continue
        pool = item.get("pool") if item.get("pool") in POOLS else "organic"
        try:
            position = int(item["position"]) if item.get("position") is not None else None
        except (TypeError, ValueError):
            position = None
        seen[listing_id] = {"listing_id": listing_id, "pool": pool, "position": position}
    if not seen:
        return 0

    column, value = ("buyer_id", buyer_id) if buyer_id else ("session_id", session_id)
    if value:
        try:
            r = (
                admin.table("listing_impressions")
                .select("listing_id")
                .in_("listing_id", list(seen))
                .gte("created_at", _since(COOLDOWN_MINUTES / 60))
                .eq(column, value)
                .limit(len(seen))
                .execute()
            )
            for row in r.data or []:
                seen.pop(str(row.get("listing_id")), None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_impressions cooldown check failed: %s", exc)
    if not seen:
        return 0

    payload = [
        {**item, "buyer_id": buyer_id, "session_id": session_id, "device_hash": device_hash}
        for item in seen.values()
    ]
    try:
        r = admin.table("listing_impressions").insert(payload).execute()
        return len(r.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_impressions insert failed: %s", exc)
        return 0


def fatigued_listing_ids(*, buyer_id: str | None, session_id: str | None, threshold: int, hours: int) -> set[str]:
    if not buyer_id and not session_id:
        return set()
    try:
        q = (
            get_supabase_admin()
            .table("listing_impressions")
            .select("listing_id")
            .gte("created_at", _since(hours))
            .limit(5000)
        )
        q = q.eq("buyer_id", buyer_id) if buyer_id else q.eq("session_id", session_id)
        rows = q.execute().data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("fatigued_listing_ids failed: %s", exc)
        return set()
    counts: dict[str, int] = {}
    for row in rows:
        pid = str(row.get("listing_id") or "")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return {pid for pid, n in counts.items() if n >= threshold}


def listing_impressions(listing_ids: list[str], *, hours: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    admin = get_supabase_admin()
    for part in chunks(listing_ids):
        try:
            r = (
                admin.table("listing_impressions")
                .select("listing_id")
                .in_("listing_id", part)
                .gte("created_at", _since(hours))
                .limit(20000)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("listing_impressions failed: %s", exc)
            continue
        for row in r.data or []:
            lid = str(row.get("listing_id") or "")
            if lid:
                counts[lid] = counts.get(lid, 0) + 1
    return counts
