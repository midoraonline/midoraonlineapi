"""Unmet demand: buyer requests and zero-result searches, plus alerts when matching listings go live."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from analytics.server_events import record_event
from feed import location
from feed.stages import lane_of

logger = logging.getLogger(__name__)

REQUEST_EVENT = "demand:requested"
ZERO_RESULTS_EVENT = "search:zero_results"
ALERT_WINDOW_DAYS = 30
_STOP = frozenset({"the", "and", "for", "with", "need", "want", "looking", "any", "who", "can", "near", "from"})


def tokens(text: Any) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) >= 3 and w not in _STOP}


def record_request(
    *, actor_id: str | None, need: str, category: str | None, area: str | None, notify: bool, client: Any = None,
) -> None:
    record_event(
        REQUEST_EVENT,
        actor_id=actor_id,
        properties={
            "need": need.strip()[:200],
            "category": (category or "").strip() or None,
            "area": area,
            "notify": bool(notify and actor_id),
        },
        client=client,
    )


def record_zero_results(
    *, actor_id: str | None, query: str, category: str | None, location_name: str | None, client: Any = None,
) -> None:
    record_event(
        ZERO_RESULTS_EVENT,
        actor_id=actor_id,
        properties={"query": query[:200], "category": category, "location": location_name},
        client=client,
    )


def matches(request: dict[str, Any], product: dict[str, Any]) -> bool:
    category = str(request.get("category") or "").strip().lower()
    product_category = str(product.get("category") or "").strip().lower()
    text = tokens(f"{product.get('title') or ''} {product_category}")
    need = tokens(request.get("need"))
    if category and category != product_category and not need & text:
        return False
    if not category and not need & text:
        return False
    area = request.get("area")
    if area and area != location.ALL_UGANDA:
        value, _, _ = location.fit(lane_of(product.get("item_type")), location.geo_of(product), area)
        return value >= 40
    return True


def notify_matches(product_id: str, client: Any = None) -> int:
    from notifications.service import create_notification

    if client is None:
        from db.supabase import get_supabase_admin

        client = get_supabase_admin()
    try:
        rows = (
            client.table("products")
            .select("id,title,category,item_type,location_name,listing_meta,shop_id")
            .eq("id", product_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return 0
        product = rows[0]
        owner = (
            client.table("shops").select("owner_id").eq("id", product.get("shop_id")).limit(1).execute().data or [{}]
        )[0].get("owner_id")
        since = (datetime.now(timezone.utc) - timedelta(days=ALERT_WINDOW_DAYS)).isoformat()
        requests = (
            client.table("analytics_events")
            .select("actor_id,properties")
            .eq("event_type", REQUEST_EVENT)
            .gte("ts", since)
            .limit(2000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("demand alert lookup failed for %s: %s", product_id, exc)
        return 0
    sent: set[str] = set()
    for req in requests:
        user = str(req.get("actor_id") or "")
        props = req.get("properties") or {}
        if not user or user in sent or user == str(owner or "") or not props.get("notify"):
            continue
        if not matches(props, product):
            continue
        sent.add(user)
        create_notification(
            user_id=user,
            title="New listing for what you need",
            body=f"“{product.get('title') or 'A listing'}” matches “{props.get('need') or 'your request'}”.",
            metadata={"type": "demand_match", "product_id": product_id},
        )
    return len(sent)


def summary(rows_requests: list[dict[str, Any]], rows_zero: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    """Aggregated demand by what and where; never exposes who asked."""
    buckets: dict[tuple[str, str, str], int] = {}
    for kind, rows, field in (("request", rows_requests, "need"), ("zero_results", rows_zero, "query")):
        for row in rows:
            props = row.get("properties") or {}
            what = str(props.get("category") or props.get(field) or "").strip().lower()
            if not what:
                continue
            where = str(props.get("area") or props.get("location") or "").strip().lower() or "anywhere"
            key = (kind, what[:80], where[:40])
            buckets[key] = buckets.get(key, 0) + 1
    ranked = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"kind": k[0], "what": k[1], "where": k[2], "count": n} for k, n in ranked]
