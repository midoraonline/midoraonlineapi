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


def _count_rows(table: str, *, filters: dict[str, Any] | None = None) -> int:
    """Cheap `count=exact` wrapper that never raises."""
    client = get_supabase_admin()
    try:
        q = client.table(table).select("id", count="exact")
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        r = q.limit(1).execute()
        return r.count if r.count is not None else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin stats count(%s) failed: %s", table, exc)
        return 0


def _fetch_all(table: str, select_cols: str) -> list[dict[str, Any]]:
    """Paginated best-effort fetch. Cap at a reasonable 5k rows."""
    client = get_supabase_admin()
    out: list[dict[str, Any]] = []
    page_size = 1000
    for page in range(5):
        try:
            r = (
                client.table(table)
                .select(select_cols)
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("admin stats fetch_all(%s) failed: %s", table, exc)
            break
        rows = r.data or []
        out.extend(rows)
        if len(rows) < page_size:
            break
    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/overview")
async def admin_stats_overview() -> dict[str, Any]:
    """Aggregate platform metrics for the admin analytics dashboard."""
    now = datetime.now(timezone.utc)
    window_days = 30
    window_start = (now - timedelta(days=window_days - 1)).date()

    shops = _fetch_all(
        "shops",
        "id, name, slug, shop_type, is_active, view_count, subscription_end_date, created_at, owner_id",
    )
    products = _fetch_all(
        "products",
        "id, shop_id, title, category, item_type, is_published, view_count, price_ugx, created_at",
    )
    users = _fetch_all("users", "id, user_role, email_verified, created_at")
    verifications = _fetch_all(
        "shop_verifications",
        "shop_id, status, requested_at, reviewed_at",
    )

    # Optional tables — we fetch defensively.
    shop_likes = _fetch_all("shop_likes", "shop_id, created_at")
    shop_follows = _fetch_all("shop_follows", "shop_id, created_at")
    product_likes = _fetch_all("product_likes", "product_id, created_at")
    orders = _fetch_all("orders", "id, shop_id, total_amount, order_status, created_at")
    subscriptions = _fetch_all(
        "subscriptions", "id, shop_id, amount, payment_status, created_at"
    )

    # --- Summary -----------------------------------------------------------
    active_shops = sum(1 for s in shops if s.get("is_active"))
    total_product_views = sum(_safe_int(p.get("view_count")) for p in products)
    total_shop_views = sum(_safe_int(s.get("view_count")) for s in shops)
    total_revenue = sum(
        _safe_float(o.get("total_amount"))
        for o in orders
        if (o.get("order_status") or "").lower() not in {"cancelled"}
    )
    total_sub_revenue = sum(
        _safe_float(s.get("amount"))
        for s in subscriptions
        if (s.get("payment_status") or "").upper() == "COMPLETED"
    )

    verif_status_map = {v["shop_id"]: (v.get("status") or "unverified") for v in verifications}
    pending_verifications = sum(1 for st in verif_status_map.values() if st == "pending")

    # Role breakdown
    role_counts: Counter = Counter(
        (u.get("user_role") or "customer").lower() for u in users
    )

    # --- Time series (last 30 days) ---------------------------------------
    def _series(source: list[dict[str, Any]], field: str = "created_at") -> list[dict[str, Any]]:
        buckets: dict[str, int] = defaultdict(int)
        for row in source:
            day = _day_bucket(row.get(field))
            if not day:
                continue
            if day >= window_start.isoformat():
                buckets[day] += 1
        # fill gaps so the chart has a continuous x-axis
        out: list[dict[str, Any]] = []
        for i in range(window_days):
            day = (window_start + timedelta(days=i)).isoformat()
            out.append({"day": day, "count": buckets.get(day, 0)})
        return out

    shops_series = _series(shops)
    products_series = _series(products)
    users_series = _series(users)
    orders_series = _series(orders)

    # --- Top shops by views -----------------------------------------------
    products_by_shop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in products:
        sid = p.get("shop_id")
        if sid:
            products_by_shop[str(sid)].append(p)

    shop_likes_by_shop: Counter = Counter(
        str(l.get("shop_id")) for l in shop_likes if l.get("shop_id")
    )
    shop_follows_by_shop: Counter = Counter(
        str(f.get("shop_id")) for f in shop_follows if f.get("shop_id")
    )

    def _shop_card(s: dict[str, Any]) -> dict[str, Any]:
        sid = str(s.get("id"))
        plist = products_by_shop.get(sid, [])
        return {
            "id": sid,
            "name": s.get("name"),
            "slug": s.get("slug"),
            "shop_type": s.get("shop_type"),
            "is_active": s.get("is_active"),
            "created_at": s.get("created_at"),
            "view_count": _safe_int(s.get("view_count")),
            "product_count": len(plist),
            "published_product_count": sum(1 for p in plist if p.get("is_published")),
            "like_count": shop_likes_by_shop.get(sid, 0),
            "follower_count": shop_follows_by_shop.get(sid, 0),
            "verification_status": verif_status_map.get(sid, "unverified"),
        }

    enriched_shops = [_shop_card(s) for s in shops]
    top_shops = sorted(
        enriched_shops, key=lambda s: s["view_count"], reverse=True
    )[:10]

    # --- Top products by views --------------------------------------------
    product_likes_by_product: Counter = Counter(
        str(l.get("product_id")) for l in product_likes if l.get("product_id")
    )
    shop_name_by_id = {str(s.get("id")): s.get("name") for s in shops}

    top_products = sorted(
        [
            {
                "id": str(p.get("id")),
                "title": p.get("title"),
                "category": p.get("category"),
                "item_type": p.get("item_type"),
                "shop_id": str(p.get("shop_id") or ""),
                "shop_name": shop_name_by_id.get(str(p.get("shop_id") or "")),
                "view_count": _safe_int(p.get("view_count")),
                "like_count": product_likes_by_product.get(str(p.get("id")), 0),
                "price_ugx": _safe_float(p.get("price_ugx")),
                "is_published": p.get("is_published"),
            }
            for p in products
        ],
        key=lambda r: r["view_count"],
        reverse=True,
    )[:10]

    # --- Distributions (pie charts) ---------------------------------------
    shop_type_counts: Counter = Counter(
        (s.get("shop_type") or "product") for s in shops
    )
    product_category_counts: Counter = Counter(
        (p.get("category") or "uncategorized").strip() or "uncategorized"
        for p in products
    )
    product_item_type_counts: Counter = Counter(
        (p.get("item_type") or "product") for p in products
    )
    verification_status_counts: Counter = Counter()
    for s in shops:
        verification_status_counts[verif_status_map.get(str(s["id"]), "unverified")] += 1
    order_status_counts: Counter = Counter(
        (o.get("order_status") or "unknown") for o in orders
    )

    return {
        "generated_at": now.isoformat(),
        "window_days": window_days,
        "summary": {
            "total_shops": len(shops),
            "active_shops": active_shops,
            "inactive_shops": len(shops) - active_shops,
            "total_products": len(products),
            "total_users": len(users),
            "total_orders": len(orders),
            "total_revenue_ugx": total_revenue,
            "total_subscription_revenue_ugx": total_sub_revenue,
            "total_shop_views": total_shop_views,
            "total_product_views": total_product_views,
            "pending_verifications": pending_verifications,
            "verified_shops": verification_status_counts.get("verified", 0),
            "rejected_shops": verification_status_counts.get("rejected", 0),
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
        "top_shops": top_shops,
        "top_products": top_products,
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
