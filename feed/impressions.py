"""Impression tracking service.

Batched inserts into `listing_impressions` — one call per user-scroll session
persists many impressions at once (client-side debounced flush).

Also exposes read helpers used by the feed engine:
  - `recent_impressions_for_user(user_id, session_id, hours)`
    → list of listing_ids the buyer has already seen recently
    (drives the "don't show what we did already" pagination behaviour +
     impression-fatigue suppression).

  - `shop_impressions_last_hours(hours)`
    → dict[shop_id, count]; used by `feed.scoring` to compute the
    exposure multiplier that gently rotates high-volume sellers off the top.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


_ALLOWED_POOLS = {
    "organic", "boosted", "sponsored", "super_boost",
    "premium_store", "fresh", "exploration",
}


def _sanitize_pool(pool: str | None) -> str:
    return pool if pool in _ALLOWED_POOLS else "organic"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def record_impressions(
    items: Iterable[dict[str, Any]],
    *,
    buyer_id: str | None,
    session_id: str | None,
    device_hash: str | None,
) -> int:
    """Insert a batch of impressions. Returns count persisted.

    Each item: `{"listing_id": str, "pool": str, "position": int?}`

    Server-side hardening:
      - Silently drops entries missing listing_id.
      - Clamps `pool` to the allowed set.
      - Deduplicates within a single batch by listing_id (client-side flush
        may include the same listing multiple times if the user scrolled
        away and back — one row per listing per batch is enough).
      - Applies a 10-minute cooldown per (buyer_id|session_id, listing_id)
        to prevent duplicate rows dominating the table when a listing
        re-enters the viewport repeatedly.
    """
    admin = get_supabase_admin()

    # Sanitize + dedupe by listing_id within this batch
    seen: dict[str, dict[str, Any]] = {}
    for item in items or []:
        listing_id = str(item.get("listing_id") or "").strip()
        if not listing_id:
            continue
        pool = _sanitize_pool(item.get("pool"))
        position = item.get("position")
        try:
            position = int(position) if position is not None else None
        except (TypeError, ValueError):
            position = None
        seen[listing_id] = {"listing_id": listing_id, "pool": pool, "position": position}

    if not seen:
        return 0

    # Cooldown check — if this buyer/session already impressed the listing in
    # the last 10 minutes, skip inserting again.
    listing_ids = list(seen.keys())
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    filter_col, filter_val = None, None
    if buyer_id:
        filter_col, filter_val = "buyer_id", buyer_id
    elif session_id:
        filter_col, filter_val = "session_id", session_id

    if filter_col and filter_val:
        try:
            q = (
                admin.table("listing_impressions")
                .select("listing_id")
                .in_("listing_id", listing_ids)
                .gte("created_at", cutoff_iso)
                .eq(filter_col, filter_val)
                .limit(len(listing_ids))
            )
            r = q.execute()
            for row in r.data or []:
                seen.pop(str(row.get("listing_id")), None)
        except Exception as exc:
            logger.warning("record_impressions cooldown check failed: %s", exc)

    if not seen:
        return 0

    payload = [
        {
            **item,
            "buyer_id": buyer_id,
            "session_id": session_id,
            "device_hash": device_hash,
        }
        for item in seen.values()
    ]

    try:
        r = admin.table("listing_impressions").insert(payload).execute()
        return len(r.data or [])
    except Exception as exc:
        logger.warning("record_impressions insert failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def recent_impressions_for_viewer(
    *,
    buyer_id: str | None,
    session_id: str | None,
    hours: int = 24,
    limit: int = 500,
) -> set[str]:
    """Return listing_ids the viewer has been shown in the last `hours`."""
    if not buyer_id and not session_id:
        return set()
    admin = get_supabase_admin()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        q = (
            admin.table("listing_impressions")
            .select("listing_id")
            .gte("created_at", cutoff_iso)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if buyer_id:
            q = q.eq("buyer_id", buyer_id)
        else:
            q = q.eq("session_id", session_id)
        r = q.execute()
        return {str(row.get("listing_id")) for row in (r.data or []) if row.get("listing_id")}
    except Exception as exc:
        logger.warning("recent_impressions_for_viewer failed: %s", exc)
        return set()


def fatigued_listing_ids(
    *,
    buyer_id: str | None,
    session_id: str | None,
    threshold: int = 3,
    hours: int = 48,
) -> set[str]:
    """Listings the viewer has already been shown `threshold` times recently.

    These are HARD-hidden from subsequent feeds (banner blindness fix).
    """
    if not buyer_id and not session_id:
        return set()
    admin = get_supabase_admin()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        q = (
            admin.table("listing_impressions")
            .select("listing_id")
            .gte("created_at", cutoff_iso)
            .limit(5000)
        )
        if buyer_id:
            q = q.eq("buyer_id", buyer_id)
        else:
            q = q.eq("session_id", session_id)
        r = q.execute()
    except Exception as exc:
        logger.warning("fatigued_listing_ids failed: %s", exc)
        return set()

    counts: dict[str, int] = {}
    for row in r.data or []:
        pid = str(row.get("listing_id") or "")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return {pid for pid, n in counts.items() if n >= threshold}


def shop_impressions_last_hours(shop_ids: list[str], hours: int = 24) -> dict[str, int]:
    """Aggregate impression counts per shop over the given window.

    Uses the products table to resolve listing_id -> shop_id since
    listing_impressions doesn't denormalise shop_id (kept minimal on
    purpose — this table is high-volume).
    """
    if not shop_ids:
        return {}
    admin = get_supabase_admin()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # 1. Pull all listing_ids owned by these shops
    try:
        r = (
            admin.table("products")
            .select("id,shop_id")
            .in_("shop_id", shop_ids)
            .limit(10000)
            .execute()
        )
    except Exception as exc:
        logger.warning("shop_impressions_last_hours (products) failed: %s", exc)
        return {}

    listing_to_shop = {str(row["id"]): str(row["shop_id"]) for row in (r.data or [])}
    listing_ids = list(listing_to_shop.keys())
    if not listing_ids:
        return {}

    # 2. Count impressions for those listings
    counts: dict[str, int] = {}
    # Process in chunks (Postgrest IN clause length limits)
    chunk = 500
    for i in range(0, len(listing_ids), chunk):
        subset = listing_ids[i : i + chunk]
        try:
            r = (
                admin.table("listing_impressions")
                .select("listing_id")
                .in_("listing_id", subset)
                .gte("created_at", cutoff_iso)
                .limit(20000)
                .execute()
            )
        except Exception as exc:
            logger.warning("shop_impressions_last_hours (impressions) failed: %s", exc)
            continue
        for row in r.data or []:
            sid = listing_to_shop.get(str(row.get("listing_id") or ""))
            if sid:
                counts[sid] = counts.get(sid, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Aggregate report (seller dashboard)
# ---------------------------------------------------------------------------

def listing_impression_report(listing_ids: list[str]) -> dict[str, dict[str, int]]:
    if not listing_ids:
        return {}
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("v_listing_impressions_agg")
            .select("*")
            .in_("listing_id", listing_ids)
            .execute()
        )
    except Exception as exc:
        logger.warning("listing_impression_report failed: %s", exc)
        return {}
    return {str(row["listing_id"]): row for row in (r.data or [])}
