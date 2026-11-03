"""Services and opportunities past their deadline are never eligible; the rebuild closes them."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from feed.stages import lane_of

logger = logging.getLogger(__name__)

DEADLINE_TYPES = ("service", "opportunity", "job")


def deadline_end(meta: Any) -> datetime | None:
    raw = str((meta or {}).get("deadline") or "").strip() if isinstance(meta, dict) else ""
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return day + timedelta(days=1)


def is_expired(row: dict[str, Any], now: datetime | None = None) -> bool:
    if lane_of(row.get("item_type")) not in ("service", "opportunity"):
        return False
    end = deadline_end(row.get("listing_meta"))
    return end is not None and (now or datetime.now(timezone.utc)) >= end


def drop_expired(rows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    return [r for r in rows if not is_expired(r, moment)]


def close_expired(db: Any, now: datetime | None = None) -> list[str]:
    try:
        rows = (
            db.table("products")
            .select("id,shop_id,title,item_type,listing_meta")
            .eq("status", "active")
            .in_("item_type", list(DEADLINE_TYPES))
            .limit(2000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_expired lookup failed: %s", exc)
        return []
    moment = now or datetime.now(timezone.utc)
    expired = [r for r in rows if r.get("id") and is_expired(r, moment)]
    ids = [str(r["id"]) for r in expired]
    if not ids:
        return []
    try:
        db.table("products").update({"status": "expired", "is_published": False, "stock_quantity": 0}).in_("id", ids).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_expired update failed: %s", exc)
        return []
    logger.info("closed %d listings past their deadline", len(ids))
    try:
        from feed.seller_nudges import notify_expired

        notify_expired(db, expired)
    except Exception as exc:  # noqa: BLE001
        logger.warning("expired listing notices failed: %s", exc)
    return ids
