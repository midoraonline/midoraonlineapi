from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def list_active_boost_plans() -> list[dict]:
    """Return all active boost plans available for purchase."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("boost_plans")
            .select("*")
            .eq("is_active", True)
            .order("price_amount")
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("list_active_boost_plans failed: %s", exc)
        return []


def get_active_boost_for_listing(listing_id: str) -> dict | None:
    """Return active boost for a listing if any."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("listing_boosts")
            .select("*, boost_plans(name, duration_hours, price_amount)")
            .eq("listing_id", listing_id)
            .eq("active", True)
            .gte("ends_at", datetime.now(timezone.utc).isoformat())
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as exc:
        logger.warning("get_active_boost_for_listing(%s) failed: %s", listing_id, exc)
        return None
