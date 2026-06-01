from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def _make_unique_key(listing_id: str, buyer_id: str | None, source: str) -> str:
    raw = f"{listing_id}:{buyer_id or 'anon'}:{source}"
    return hashlib.sha256(raw.encode()).hexdigest()


def record_lead_event(
    listing_id: str,
    seller_id: str,
    buyer_id: str | None = None,
    source: str = "whatsapp",
    metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Record a lead event with deduplication via unique_key."""
    admin = get_supabase_admin()
    unique_key = _make_unique_key(listing_id, buyer_id, source)

    existing = (
        admin.table("lead_events")
        .select("id")
        .eq("unique_key", unique_key)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    payload = {
        "listing_id": listing_id,
        "seller_id": seller_id,
        "buyer_id": buyer_id,
        "source": source,
        "lead_status": "new",
        "unique_key": unique_key,
        "metadata": metadata or {},
    }
    r = admin.table("lead_events").insert(payload).execute()
    return r.data[0] if r.data else None


def list_leads_for_seller(
    seller_id: str,
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
) -> dict:
    """Paginated lead list for a seller dashboard."""
    admin = get_supabase_admin()
    limit = min(limit, 100)
    offset = (page - 1) * limit

    q = (
        admin.table("lead_events")
        .select("*, products(title, price_ugx, image_urls)", count="exact")
        .eq("seller_id", seller_id)
    )
    if status:
        q = q.eq("lead_status", status)

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0

    return {
        "items": r.data or [],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_lead_stats_for_seller(seller_id: str) -> dict:
    """Aggregated lead stats for seller dashboard."""
    admin = get_supabase_admin()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    try:
        total = (
            admin.table("lead_events")
            .select("id", count="exact")
            .eq("seller_id", seller_id)
            .limit(1)
            .execute()
        )
        today = (
            admin.table("lead_events")
            .select("id", count="exact")
            .eq("seller_id", seller_id)
            .gte("created_at", today_start)
            .limit(1)
            .execute()
        )
        new_count = (
            admin.table("lead_events")
            .select("id", count="exact")
            .eq("seller_id", seller_id)
            .eq("lead_status", "new")
            .limit(1)
            .execute()
        )

        return {
            "total_leads": total.count or 0,
            "today_leads": today.count or 0,
            "new_leads": new_count.count or 0,
        }
    except Exception as exc:
        logger.warning("get_lead_stats_for_seller(%s) failed: %s", seller_id, exc)
        return {"total_leads": 0, "today_leads": 0, "new_leads": 0}


def update_lead_status(lead_id: str, status: str) -> dict | None:
    """Update the status of a lead (responded, ignored, closed)."""
    admin = get_supabase_admin()
    r = (
        admin.table("lead_events")
        .update({"lead_status": status})
        .eq("id", lead_id)
        .execute()
    )
    return r.data[0] if r.data else None
