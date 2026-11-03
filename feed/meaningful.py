"""Edits that genuinely change a listing may refresh its freshness clock, at most once per cooldown."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from feed import config as C
from feed.signals import parse_timestamp

logger = logging.getLogger(__name__)

PRICE_CHANGE_SHARE = 0.10
DESCRIPTION_GROWTH_CHARS = 80
CLOSED = frozenset({"sold", "unavailable", "filled", "closed", "hidden", "out_of_stock"})


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("listing_meta")
    return meta if isinstance(meta, dict) else {}


def is_meaningful(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old_price = float(before.get("price_ugx") or 0)
    new_price = float(after.get("price_ugx") or 0)
    if "price_ugx" in after and new_price > 0:
        if old_price <= 0 or abs(new_price - old_price) / old_price >= PRICE_CHANGE_SHARE:
            return True
    if "image_urls" in after:
        old = set(before.get("image_urls") or [])
        if any(url not in old for url in after.get("image_urls") or []):
            return True
    if "description" in after:
        grown = len(str(after.get("description") or "").strip()) - len(str(before.get("description") or "").strip())
        if grown >= DESCRIPTION_GROWTH_CHARS:
            return True
    if "listing_meta" in after:
        old_deadline = str(_meta(before).get("deadline") or "")
        new_deadline = str(_meta(after).get("deadline") or "")
        if new_deadline and new_deadline > old_deadline:
            return True
    if "stock_quantity" in after and int(before.get("stock_quantity") or 0) <= 0 < int(after.get("stock_quantity") or 0):
        return True
    return str(before.get("status") or "") in CLOSED and after.get("status") == "active"


def refresh_allowed(created_at: Any, now: datetime | None = None) -> bool:
    last = parse_timestamp(created_at)
    moment = now or datetime.now(timezone.utc)
    return last is None or moment - last >= timedelta(hours=C.FRESHNESS_RESET_COOLDOWN_HOURS)


def maybe_refresh(db: Any, product_id: str, before: dict[str, Any], after: dict[str, Any]) -> bool:
    if not is_meaningful(before, after) or not refresh_allowed(before.get("created_at")):
        return False
    now = datetime.now(timezone.utc).isoformat()
    patch: dict[str, Any] = {"created_at": now}
    if not before.get("first_posted_at"):
        patch["first_posted_at"] = before.get("created_at")
    try:
        db.table("products").update(patch).eq("id", product_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("freshness refresh failed for %s: %s", product_id, exc)
        return False
    return True
