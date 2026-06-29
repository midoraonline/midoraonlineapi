from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/online-users")
async def get_online_users() -> dict[str, Any]:
    """Count users who had any activity in the last 15 minutes.

    Uses `listing_events` (buyer_id) as the primary signal — any view,
    WhatsApp click, save or message means the user was browsing. Falls
    back to unique visitors from `search_history` within the same window
    and merges the two sets so nobody is double-counted.

    This deliberately does NOT require authentication so the navbar can
    show the count to anonymous visitors.
    """
    admin = get_supabase_admin()
    window_minutes = 15
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()

    active_user_ids: set[str] = set()

    # --- Signal 1: listing_events (views, clicks, messages) ---
    try:
        ev_r = (
            admin.table("listing_events")
            .select("buyer_id")
            .gte("created_at", since)
            .not_.is_("buyer_id", "null")
            .limit(2000)
            .execute()
        )
        for row in ev_r.data or []:
            uid = row.get("buyer_id")
            if uid:
                active_user_ids.add(str(uid))
    except Exception as exc:
        logger.warning("online-users listing_events query failed: %s", exc)

    # --- Signal 2: search_history ---
    try:
        sh_r = (
            admin.table("search_history")
            .select("user_id")
            .gte("created_at", since)
            .not_.is_("user_id", "null")
            .limit(2000)
            .execute()
        )
        for row in sh_r.data or []:
            uid = row.get("user_id")
            if uid:
                active_user_ids.add(str(uid))
    except Exception as exc:
        logger.warning("online-users search_history query failed: %s", exc)

    # --- Signal 3: product_likes (recent activity) ---
    try:
        lk_r = (
            admin.table("product_likes")
            .select("user_id")
            .gte("created_at", since)
            .not_.is_("user_id", "null")
            .limit(1000)
            .execute()
        )
        for row in lk_r.data or []:
            uid = row.get("user_id")
            if uid:
                active_user_ids.add(str(uid))
    except Exception as exc:
        logger.warning("online-users product_likes query failed: %s", exc)

    count = len(active_user_ids)

    # Sanity floor: if no real signals yet (empty DB / early stage),
    # return at least 1 so the UI doesn't show "0 online".
    if count == 0:
        count = 1

    return {
        "online_count": count,
        "window_minutes": window_minutes,
    }

