"""Gentle seller nudges: expired deadlines and products that look like they've sold out."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from feed.signals import parse_timestamp

logger = logging.getLogger(__name__)

VIEWS_PER_STOCK = 4
CONTACTS_PER_STOCK = 3
WINDOW_DAYS = 14
COOLDOWN_DAYS = 7
STOCK_NUDGE = "stock_nudge"
EXPIRED = "listing_expired"


def _owners(db: Any, shop_ids: list[str]) -> dict[str, str]:
    if not shop_ids:
        return {}
    try:
        rows = db.table("shops").select("id,owner_id").in_("id", shop_ids).execute().data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("nudge owner lookup failed: %s", exc)
        return {}
    return {str(r["id"]): str(r["owner_id"]) for r in rows if r.get("id") and r.get("owner_id")}


def _send(user_id: str, title: str, body: str, product_id: str, kind: str, extra: dict[str, Any] | None = None) -> None:
    from auth.preferences import notification_enabled
    from notifications.push_service import send_to_user
    from notifications.service import create_notification

    url = f"/merchant/listings/{product_id}/edit"
    create_notification(
        user_id=user_id,
        title=title,
        body=body,
        metadata={"type": kind, "product_id": product_id, "url": url, **(extra or {})},
    )
    if notification_enabled(user_id, "listing_updates", "push"):
        try:
            send_to_user(user_id, {"title": title, "body": body, "url": url, "data": {"product_id": product_id}})
        except Exception as exc:  # noqa: BLE001
            logger.info("nudge push failed for %s: %s", user_id, exc)


def notify_expired(db: Any, rows: list[dict[str, Any]]) -> int:
    owners = _owners(db, list({str(r.get("shop_id")) for r in rows if r.get("shop_id")}))
    sent = 0
    for row in rows:
        owner = owners.get(str(row.get("shop_id") or ""))
        if not owner:
            continue
        title = row.get("title") or "Your listing"
        is_service = str(row.get("item_type") or "").lower() == "service"
        _send(
            owner,
            "Your listing's deadline has passed",
            f"“{title}” passed its deadline, so we've taken it off the feed. "
            + (
                "Still taking clients? Set a new date and mark it available again."
                if is_service
                else "Still open? Set a new deadline and mark it available again."
            ),
            str(row["id"]),
            EXPIRED,
        )
        sent += 1
    return sent


def attention_for(db: Any, user_id: str, days: int = WINDOW_DAYS) -> dict[str, dict[str, str]]:
    """listing id -> {kind, message} from this seller's recent nudges."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = (
            db.table("notifications")
            .select("body,metadata,created_at")
            .eq("user_id", user_id)
            .in_("metadata->>type", [STOCK_NUDGE, EXPIRED])
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("attention lookup failed: %s", exc)
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        pid = str(meta.get("product_id") or "")
        if pid and pid not in out:
            out[pid] = {"kind": str(meta.get("type")), "message": str(row.get("body") or ""), "at": str(row.get("created_at") or "")}
            if meta.get("stock") is not None:
                out[pid]["stock"] = str(meta.get("stock"))
    return out


def stock_nudge_reason(stock: int, views: int, contacts: int) -> str | None:
    if stock <= 0:
        return None
    if contacts >= CONTACTS_PER_STOCK * stock:
        return "contacts"
    if views >= VIEWS_PER_STOCK * stock:
        return "views"
    return None


def _recent_nudges(db: Any, since: datetime) -> dict[str, datetime]:
    try:
        rows = (
            db.table("notifications")
            .select("metadata,created_at")
            .eq("metadata->>type", STOCK_NUDGE)
            .gte("created_at", since.isoformat())
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent nudge lookup failed: %s", exc)
        return {}
    out: dict[str, datetime] = {}
    for row in rows:
        pid = str((row.get("metadata") or {}).get("product_id") or "")
        at = parse_timestamp(row.get("created_at"))
        if pid and at and (pid not in out or at > out[pid]):
            out[pid] = at
    return out


def run_stock_nudges(db: Any, now: datetime | None = None) -> int:
    """Ask sellers to check stock when interest is high compared with what's left."""
    from shop import listing_stats

    moment = now or datetime.now(timezone.utc)
    try:
        products = (
            db.table("products")
            .select("id,shop_id,title,item_type,stock_quantity,created_at")
            .eq("status", "active")
            .eq("is_published", True)
            .gt("stock_quantity", 0)
            .limit(2000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock nudge lookup failed: %s", exc)
        return 0
    products = [p for p in products if str(p.get("item_type") or "product").lower() in ("product", "property", "")]
    if not products:
        return 0
    window_start = moment - timedelta(days=WINDOW_DAYS)
    nudged = _recent_nudges(db, window_start)
    due = [p for p in products if str(p["id"]) not in nudged or nudged[str(p["id"])] <= moment - timedelta(days=COOLDOWN_DAYS)]
    if not due:
        return 0
    stats = listing_stats.by_listing(db, [str(p["id"]) for p in due if str(p["id"]) not in nudged], window_start)
    for p in due:
        pid = str(p["id"])
        if pid in nudged:
            stats.update(listing_stats.by_listing(db, [pid], nudged[pid]))
    owners = _owners(db, list({str(p.get("shop_id")) for p in due if p.get("shop_id")}))
    sent = 0
    for product in due:
        pid = str(product["id"])
        row = stats.get(pid) or {}
        views = int(row.get("views") or 0)
        contacts = int(row.get("whatsapp_clicks") or 0) + int(row.get("messages") or 0) + int(row.get("call_clicks") or 0)
        stock = int(product.get("stock_quantity") or 0)
        reason = stock_nudge_reason(stock, views, contacts)
        owner = owners.get(str(product.get("shop_id") or ""))
        if not reason or not owner:
            continue
        title = product.get("title") or "Your listing"
        detail = f"{contacts} buyers contacted you" if reason == "contacts" else f"it was viewed {views} times"
        _send(
            owner,
            "Is this still in stock?",
            f"“{title}” shows {stock} left and {detail} recently. "
            "If some have sold, update the stock so buyers see what's really available.",
            pid,
            STOCK_NUDGE,
            {"stock": stock},
        )
        sent += 1
    return sent
