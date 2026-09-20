"""Fan-out analytics_events into ranking / dashboard tables.

The browser only writes POST /analytics/events. Merchant dashboards, feed
scoring, and view counters still read listing_impressions, listing_events,
and view_count — ingest materializes those so one event is the source of truth.
"""
from __future__ import annotations

import logging
from typing import Any

from db.supabase import get_supabase_admin
from feed.impressions import record_impressions
from feed.service import invalidate_user_feed_cache
from shop.engagement_service import record_product_view, record_shop_view

logger = logging.getLogger(__name__)

_STRONG_INTENT = frozenset({"whatsapp_clicked", "messaged", "saved"})


def materialize_ingested_events(
    rows: list[dict[str, Any]],
    *,
    actor_id: str | None,
) -> None:
    if not rows:
        return

    impressions: list[dict[str, Any]] = []
    session_id: str | None = None
    for row in rows:
        sid = row.get("session_id")
        if isinstance(sid, str) and sid:
            session_id = sid
        event_type = str(row.get("event_type") or "")
        if event_type == "listing:impressed":
            listing_id = str(row.get("target_id") or "").strip()
            if listing_id:
                props = row.get("properties") or {}
                impressions.append(
                    {
                        "listing_id": listing_id,
                        "pool": props.get("pool"),
                        "position": props.get("position"),
                    }
                )
            continue
        try:
            _materialize_one(row, actor_id=actor_id)
        except Exception as exc:
            logger.warning("analytics materialize skipped %s: %s", event_type, exc)

    if impressions:
        record_impressions(
            impressions,
            buyer_id=actor_id,
            session_id=session_id,
            device_hash=None,
        )


def _materialize_one(row: dict[str, Any], *, actor_id: str | None) -> None:
    event_type = str(row.get("event_type") or "")
    props = row.get("properties") or {}
    session_id = row.get("session_id")
    product_id = _opt_str(props.get("productId"))
    shop_id = _opt_str(props.get("shopId"))
    target_id = _opt_str(row.get("target_id"))
    admin = get_supabase_admin()

    if event_type == "listing:viewed":
        product_id = product_id or target_id
        if not product_id:
            return
        record_product_view(admin, product_id, buyer_id=actor_id)
        _score_listing(product_id)
        return

    if event_type == "shop:viewed" and shop_id:
        record_shop_view(admin, shop_id)
        return

    if event_type == "conversion:whatsapp_click":
        _write_listing_event(
            admin,
            event_type="whatsapp_clicked",
            product_id=product_id,
            shop_id=shop_id,
            buyer_id=actor_id,
            session_id=session_id,
            metadata={"source": props.get("clickSource") or "analytics"},
        )
        return

    if event_type == "listing:messaged":
        _write_listing_event(
            admin,
            event_type="messaged",
            product_id=product_id,
            shop_id=shop_id,
            buyer_id=actor_id,
            session_id=session_id,
            metadata={"source": "analytics"},
        )
        return

    if event_type == "listing:favorited":
        product_id = product_id or target_id
        if not product_id:
            return
        _write_listing_event(
            admin,
            event_type="saved",
            product_id=product_id,
            shop_id=shop_id,
            buyer_id=actor_id,
            session_id=session_id,
            metadata={"source": "analytics"},
        )


def _write_listing_event(
    admin: Any,
    *,
    event_type: str,
    product_id: str | None,
    shop_id: str | None,
    buyer_id: str | None,
    session_id: str | None,
    metadata: dict[str, Any],
) -> None:
    listing_id = product_id
    seller_id = _seller_id(admin, listing_id=listing_id, shop_id=shop_id)
    if listing_id is None and not shop_id:
        return
    payload: dict[str, Any] = {
        "listing_id": listing_id,
        "seller_id": seller_id,
        "buyer_id": buyer_id,
        "session_id": session_id,
        "event_type": event_type,
        "metadata": {**metadata, **({"shop_id": shop_id} if shop_id else {})},
    }
    admin.table("listing_events").insert(payload).execute()
    if listing_id:
        _score_listing(listing_id)
    if buyer_id and event_type in _STRONG_INTENT:
        invalidate_user_feed_cache(admin, buyer_id)


def _seller_id(
    admin: Any,
    *,
    listing_id: str | None,
    shop_id: str | None,
) -> str | None:
    resolved_shop = shop_id
    if listing_id and not resolved_shop:
        product_r = (
            admin.table("products").select("shop_id").eq("id", listing_id).limit(1).execute()
        )
        if product_r.data:
            resolved_shop = str(product_r.data[0].get("shop_id") or "") or None
    if not resolved_shop:
        return None
    shop_r = (
        admin.table("shops").select("owner_id").eq("id", resolved_shop).limit(1).execute()
    )
    if not shop_r.data:
        return None
    owner = shop_r.data[0].get("owner_id")
    return str(owner) if owner else None


def _score_listing(product_id: str) -> None:
    from ranking.service import calculate_listing_score

    calculate_listing_score(product_id)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
