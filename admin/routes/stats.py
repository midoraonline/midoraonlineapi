"""Admin platform statistics.

Returns pre-aggregated metrics the admin dashboard turns into charts. All
queries go through the service-role supabase client so RLS is bypassed.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from db.supabase import get_supabase_admin

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _day_bucket(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except ValueError:
        return None


def _count(table: str, *, filters: dict[str, Any] | None = None) -> int:
    """PostgREST exact count — fast index scan, not table scan."""
    client = get_supabase_admin()
    try:
        q = client.table(table).select("id", count="exact")
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        r = q.limit(1).execute()
        return r.count if r.count is not None else 0
    except Exception as exc:
        logger.warning("admin stats count(%s) failed: %s", table, exc)
        return 0


def _get(table: str, select: str, **filters) -> list[dict[str, Any]]:
    """Fetch rows with optional equality filters."""
    client = get_supabase_admin()
    try:
        q = client.table(table).select(select)
        for col, val in filters.items():
            if val is not None:
                q = q.eq(col, val)
        return (q.execute()).data or []
    except Exception as exc:
        logger.warning("admin stats get(%s) failed: %s", table, exc)
        return []


def _get_since(table: str, select: str, days: int = 30, **filters) -> list[dict[str, Any]]:
    """Fetch rows created within the last N days, plus optional filters."""
    client = get_supabase_admin()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        q = client.table(table).select(select).gte("created_at", cutoff)
        for col, val in filters.items():
            if val is not None:
                q = q.eq(col, val)
        return (q.execute()).data or []
    except Exception as exc:
        logger.warning("admin stats get_since(%s) failed: %s", table, exc)
        return []


def _get_top(
    table: str, select: str, order_by: str, limit: int = 10, desc: bool = True,
) -> list[dict[str, Any]]:
    """Fetch top N rows ordered by a column — database-level sort + limit."""
    client = get_supabase_admin()
    try:
        q = (
            client.table(table)
            .select(select)
            .order(order_by, desc=desc)
            .limit(limit)
        )
        return (q.execute()).data or []
    except Exception as exc:
        logger.warning("admin stats get_top(%s) failed: %s", table, exc)
        return []


def _series(
    rows: list[dict[str, Any]],
    window_start: datetime.date,
    window_days: int,
    field: str = "created_at",
) -> list[dict[str, Any]]:
    """Build a zero-filled day bucket array for charting."""
    buckets: dict[str, int] = defaultdict(int)
    for row in rows:
        day = _day_bucket(row.get(field))
        if not day:
            continue
        if day >= window_start.isoformat():
            buckets[day] += 1
    out: list[dict[str, Any]] = []
    for i in range(window_days):
        day_iso = (window_start + timedelta(days=i)).isoformat()
        out.append({"day": day_iso, "count": buckets.get(day_iso, 0)})
    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/overview")
def admin_stats_overview() -> dict[str, Any]:
    """Aggregate platform metrics for the admin analytics dashboard.

    Uses targeted queries (count=exact, date-filtered, DB-level sort + limit)
    instead of fetching all rows and aggregating in Python.
    """
    now = datetime.now(timezone.utc)
    window_days = 30
    window_start = (now - timedelta(days=window_days - 1)).date()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    admin = get_supabase_admin()

    # ── Summary counts (fast with PK index) ──────────────────────────────
    total_shops = _count("shops")
    active_shops_count = _count("shops", filters={"is_active": True})
    total_products = _count("products")
    total_users = _count("users")
    total_orders = _count("orders")
    pending_verifications = _count("shop_verifications", filters={"status": "pending"})
    verified_shops = _count("shop_verifications", filters={"status": "verified"})
    rejected_shops = _count("shop_verifications", filters={"status": "rejected"})

    # ── Revenue ──────────────────────────────────────────────────────────
    try:
        orders_raw = (
            admin.table("orders")
            .select("total_amount, order_status, created_at")
            .not_.eq("order_status", "cancelled")
            .execute()
        )
        orders_list = orders_raw.data or []
    except Exception as exc:
        logger.warning("admin stats orders failed: %s", exc)
        orders_list = []

    total_revenue = sum(_safe_float(o.get("total_amount")) for o in orders_list)

    try:
        subs_raw = (
            admin.table("subscriptions")
            .select("amount")
            .eq("payment_status", "COMPLETED")
            .execute()
        )
        total_sub_revenue = sum(
            _safe_float(s.get("amount")) for s in (subs_raw.data or [])
        )
    except Exception as exc:
        logger.warning("admin stats subscriptions failed: %s", exc)
        total_sub_revenue = 0.0

    # ── View counts (lightweight — just one column) ──────────────────────
    try:
        shops_views = admin.table("shops").select("view_count").execute()
        total_shop_views = sum(
            _safe_int(s.get("view_count")) for s in (shops_views.data or [])
        )
    except Exception as exc:
        logger.warning("admin stats shop views failed: %s", exc)
        total_shop_views = 0

    try:
        products_views = admin.table("products").select("view_count").execute()
        total_product_views = sum(
            _safe_int(p.get("view_count")) for p in (products_views.data or [])
        )
    except Exception as exc:
        logger.warning("admin stats product views failed: %s", exc)
        total_product_views = 0

    # ── Role breakdown (one column only) ─────────────────────────────────
    try:
        users_raw = admin.table("users").select("user_role").execute()
        role_counts: Counter = Counter(
            (u.get("user_role") or "customer").lower()
            for u in (users_raw.data or [])
        )
    except Exception as exc:
        logger.warning("admin stats users failed: %s", exc)
        role_counts = Counter()

    # ── Time series (last 30 days only — date-filtered at DB level) ──────
    recent_shops = _get_since("shops", "id, created_at")
    recent_products = _get_since("products", "id, created_at")
    recent_users = _get_since("users", "id, created_at")

    cutoff = (now - timedelta(days=window_days)).isoformat()
    recent_orders = [
        o for o in orders_list
        if (o.get("created_at") or "") >= cutoff
    ]

    shops_series = _series(recent_shops, window_start, window_days)
    products_series = _series(recent_products, window_start, window_days)
    users_series = _series(recent_users, window_start, window_days)
    orders_series = _series(recent_orders, window_start, window_days)

    # ── Top shops (DB-level sort + limit) ────────────────────────────────
    top_shops_raw = _get_top(
        "shops",
        "id, name, slug, shop_type, is_active, view_count, created_at, owner_id",
        "view_count",
        limit=10,
    )

    top_shop_ids = [s["id"] for s in top_shops_raw]
    if top_shop_ids:
        try:
            vs = (
                admin.table("shop_verifications")
                .select("shop_id, status")
                .in_("shop_id", top_shop_ids)
                .execute()
            ).data or []
        except Exception:
            vs = []
    else:
        vs = []

    # ── Top products (DB-level sort + limit) ─────────────────────────────
    top_products_raw = _get_top(
        "products",
        "id, shop_id, title, category, item_type, is_published, view_count, price_ugx, created_at",
        "view_count",
        limit=10,
    )

    top_product_shop_ids = list(
        {str(p.get("shop_id", "")) for p in top_products_raw if p.get("shop_id")}
    )
    shop_name_map: dict[str, str] = {}
    if top_product_shop_ids:
        try:
            shops_lookup = (
                admin.table("shops")
                .select("id, name")
                .in_("id", top_product_shop_ids)
                .execute()
            ).data or []
            for s in shops_lookup:
                shop_name_map[str(s["id"])] = s.get("name", "")
        except Exception:
            pass

    # ── Distributions (fetch only the grouping column) ───────────────────
    shop_types_all = _get("shops", "shop_type")
    shop_type_counts: Counter = Counter(
        (s.get("shop_type") or "product") for s in shop_types_all
    )

    categories_all = _get("products", "category")
    product_category_counts: Counter = Counter(
        (p.get("category") or "uncategorized").strip() or "uncategorized"
        for p in categories_all
    )

    item_types_all = _get("products", "item_type")
    product_item_type_counts: Counter = Counter(
        (p.get("item_type") or "product") for p in item_types_all
    )

    all_verifs = _get("shop_verifications", "shop_id, status")
    verif_dist_map: dict[str, str] = {
        str(v["shop_id"]): v.get("status", "unverified") for v in all_verifs
    }
    verification_status_counts = Counter(verif_dist_map.values())
    unverified_count = total_shops - sum(verification_status_counts.values())
    if unverified_count > 0:
        verification_status_counts["unverified"] = unverified_count

    order_status_all = _get("orders", "order_status")
    order_status_counts: Counter = Counter(
        (o.get("order_status") or "unknown") for o in order_status_all
    )

    # ── Additional metrics (best-effort) ─────────────────────────────────
    whatsapp_clicks_today = 0
    try:
        listing_events = (
            admin.table("listing_events")
            .select("event_type, created_at")
            .gte("created_at", today_start)
            .execute()
        )
        whatsapp_clicks_today = sum(
            1 for e in (listing_events.data or [])
            if e.get("event_type") == "whatsapp_clicked"
        )
    except Exception:
        pass

    boost_revenue_today = 0.0
    boost_purchases_today = 0
    try:
        boosts_today = (
            admin.table("listing_boosts")
            .select("id, payment_status, created_at, boost_plan_id, seller_id")
            .gte("created_at", today_start)
            .execute()
        )
        boosts = boosts_today.data or []
        boost_purchases_today = len(boosts)
        if boosts:
            plan_ids = list({b["boost_plan_id"] for b in boosts if b.get("boost_plan_id")})
            if plan_ids:
                plans = (
                    admin.table("boost_plans")
                    .select("id, price_amount")
                    .in_("id", plan_ids)
                    .execute()
                )
                prices = {
                    str(p["id"]): float(p.get("price_amount", 0))
                    for p in (plans.data or [])
                }
                boost_revenue_today = sum(
                    prices.get(str(b.get("boost_plan_id")), 0)
                    for b in boosts
                    if b.get("payment_status") == "completed"
                )
    except Exception:
        pass

    active_boosts_count = _count("listing_boosts", filters={"active": True})
    fraud_alert_count = _count("fraud_flags", filters={"resolved": False})
    total_reports = _count("product_reports", filters={"resolved": False})
    total_flagged_product_comments = _count("product_comments", filters={"is_flagged": True})
    total_flagged_shop_comments = _count("shop_comments", filters={"is_flagged": True})
    total_flagged_comments = total_flagged_product_comments + total_flagged_shop_comments

    total_conversations = _count("conversations")
    total_messages = _count("messages")

    # ── Build enriched top shops ─────────────────────────────────────────
    enriched_top_shops = []
    for s in top_shops_raw:
        sid = str(s["id"])
        enriched_top_shops.append({
            "id": sid,
            "name": s.get("name"),
            "slug": s.get("slug"),
            "shop_type": s.get("shop_type"),
            "is_active": s.get("is_active"),
            "created_at": s.get("created_at"),
            "view_count": _safe_int(s.get("view_count")),
            "product_count": 0,
            "published_product_count": 0,
            "like_count": 0,
            "follower_count": 0,
            "verification_status": verif_dist_map.get(sid, "unverified"),
        })

    # ── Build enriched top products ──────────────────────────────────────
    enriched_top_products = []
    for p in top_products_raw:
        enriched_top_products.append({
            "id": str(p.get("id", "")),
            "title": p.get("title"),
            "category": p.get("category"),
            "item_type": p.get("item_type"),
            "shop_id": str(p.get("shop_id", "")),
            "shop_name": shop_name_map.get(str(p.get("shop_id", "")), ""),
            "view_count": _safe_int(p.get("view_count")),
            "like_count": 0,
            "price_ugx": _safe_float(p.get("price_ugx")),
            "is_published": p.get("is_published"),
        })

    return {
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "summary": {
            "total_shops": total_shops,
            "active_shops": active_shops_count,
            "inactive_shops": total_shops - active_shops_count,
            "total_products": total_products,
            "total_users": total_users,
            "total_orders": total_orders,
            "total_revenue_ugx": total_revenue,
            "total_subscription_revenue_ugx": total_sub_revenue,
            "total_shop_views": total_shop_views,
            "total_product_views": total_product_views,
            "pending_verifications": pending_verifications,
            "verified_shops": verified_shops,
            "rejected_shops": rejected_shops,
            "whatsapp_clicks_today": whatsapp_clicks_today,
            "boost_revenue_today_ugx": boost_revenue_today,
            "boost_purchases_today": boost_purchases_today,
            "active_boosts": active_boosts_count,
            "fraud_alerts": fraud_alert_count,
            "total_reports": total_reports,
            "total_flagged_comments": total_flagged_comments,
            "total_conversations": total_conversations,
            "total_messages": total_messages,
        },
        "role_breakdown": [
            {"role": role, "count": n} for role, n in role_counts.most_common()
        ],
        "trends": {
            "shops": shops_series,
            "products": products_series,
            "users": users_series,
            "orders": orders_series,
        },
        "top_shops": enriched_top_shops,
        "top_products": enriched_top_products,
        "distributions": {
            "shop_types": [
                {"label": k, "value": v} for k, v in shop_type_counts.most_common()
            ],
            "product_categories": [
                {"label": k, "value": v}
                for k, v in product_category_counts.most_common(10)
            ],
            "product_item_types": [
                {"label": k, "value": v}
                for k, v in product_item_type_counts.most_common()
            ],
            "verification_status": [
                {"label": k, "value": v}
                for k, v in verification_status_counts.most_common()
            ],
            "order_status": [
                {"label": k, "value": v} for k, v in order_status_counts.most_common()
            ],
        },
    }
