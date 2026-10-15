from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _rows(admin, table: str, cols: str, column: str, ids: list[str]) -> list[dict[str, Any]]:
    ids = [i for i in dict.fromkeys(str(x) for x in ids if x)]
    if not ids:
        return []
    try:
        return admin.table(table).select(cols).in_(column, ids).execute().data or []
    except Exception as exc:  # noqa: BLE001 - enrichment is best effort
        logger.warning("report enrichment %s failed: %s", table, exc)
        return []


def _first_image(row: dict[str, Any]) -> str | None:
    urls = row.get("image_urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split(",") if u.strip()]
    return urls[0] if urls else None


def _reporters(admin, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = _rows(admin, "users", "id, full_name, email", "id", [r.get("reporter_id") for r in items])
    return {str(u["id"]): {"id": str(u["id"]), "full_name": u.get("full_name"), "email": u.get("email")} for u in rows}


def enrich_product_reports(admin, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach listing (thumb, title, price, type, status), seller and reporter."""
    products = {
        str(p["id"]): p
        for p in _rows(
            admin,
            "products",
            "id, title, price_ugx, discount_price, item_type, status, image_urls, shop_id",
            "id",
            [r.get("product_id") for r in items],
        )
    }
    shops = {
        str(s["id"]): s
        for s in _rows(admin, "shops", "id, name, slug, owner_id", "id", [p.get("shop_id") for p in products.values()])
    }
    owners = {
        str(u["id"]): u
        for u in _rows(admin, "users", "id, full_name", "id", [s.get("owner_id") for s in shops.values()])
    }
    reporters = _reporters(admin, items)
    out = []
    for r in items:
        p = products.get(str(r.get("product_id")))
        listing = None
        if p:
            shop = shops.get(str(p.get("shop_id"))) or {}
            owner = owners.get(str(shop.get("owner_id"))) or {}
            listing = {
                "id": str(p["id"]),
                "title": p.get("title"),
                "price_ugx": p.get("price_ugx"),
                "discount_price": p.get("discount_price"),
                "item_type": p.get("item_type") or "product",
                "status": p.get("status"),
                "thumbnail_url": _first_image(p),
                "shop_name": shop.get("name"),
                "shop_slug": shop.get("slug"),
                "seller_name": owner.get("full_name") or shop.get("name"),
            }
        out.append({**r, "listing": listing, "reporter": reporters.get(str(r.get("reporter_id")))})
    return out


def enrich_seller_reports(admin, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the reported seller's business (name, logo, verified, listing count) and reporter."""
    seller_ids = [r.get("seller_id") for r in items]
    users = {str(u["id"]): u for u in _rows(admin, "users", "id, full_name, email", "id", seller_ids)}
    shops_by_owner: dict[str, list[dict[str, Any]]] = {}
    for s in _rows(admin, "shops", "id, name, slug, logo_url, owner_id, trust_badges, is_active", "owner_id", seller_ids):
        shops_by_owner.setdefault(str(s.get("owner_id")), []).append(s)
    all_shop_ids = [s["id"] for lst in shops_by_owner.values() for s in lst]
    counts: dict[str, int] = {}
    for p in _rows(admin, "products", "id, shop_id", "shop_id", all_shop_ids):
        counts[str(p.get("shop_id"))] = counts.get(str(p.get("shop_id")), 0) + 1
    reporters = _reporters(admin, items)
    out = []
    for r in items:
        sid = str(r.get("seller_id"))
        user = users.get(sid) or {}
        owned = shops_by_owner.get(sid, [])
        main = next((s for s in owned if s.get("is_active")), owned[0] if owned else None)
        badges = {b for s in owned for b in (s.get("trust_badges") or [])}
        seller = {
            "id": sid,
            "name": (main or {}).get("name") or user.get("full_name") or "Unknown seller",
            "owner_name": user.get("full_name"),
            "logo_url": (main or {}).get("logo_url"),
            "slug": (main or {}).get("slug"),
            "verified": bool(badges & {"identity_verified", "business_verified"}),
            "listing_count": sum(counts.get(str(s["id"]), 0) for s in owned),
            "business_count": len(owned),
        }
        out.append({**r, "seller": seller, "reporter": reporters.get(str(r.get("reporter_id")))})
    return out


@router.get("/reports")
def list_reports(
    resolved: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        count_q = (
            admin.table("product_reports")
            .select("id", count="exact")
        )
        if resolved is not None:
            count_q = count_q.eq("resolved", resolved)
        count_r = count_q.limit(1).execute()
        total = count_r.count if count_r.count is not None else 0

        query = (
            admin.table("product_reports")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if resolved is not None:
            query = query.eq("resolved", resolved)
        offset = (page - 1) * limit
        if offset:
            query = query.offset(offset)
        r = query.execute()
        return {"items": enrich_product_reports(admin, r.data or []), "total": total}
    except Exception as exc:
        logger.warning("list_reports failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}


@router.patch("/reports/{report_id}/resolve")
def resolve_report(report_id: str) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        admin.table("product_reports").update({"resolved": True}).eq("id", report_id).execute()
        return {"status": "resolved"}
    except Exception as exc:
        logger.warning("resolve_report failed: %s", exc)
        return {"error": "Failed to resolve report"}


@router.get("/seller-reports")
def list_seller_reports(
    resolved: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        q = admin.table("seller_reports").select("*", count="exact").order("created_at", desc=True).limit(limit)
        if resolved is not None:
            q = q.eq("resolved", resolved)
        offset = (page - 1) * limit
        if offset:
            q = q.offset(offset)
        r = q.execute()
        return {"items": enrich_seller_reports(admin, r.data or []), "total": r.count if r.count is not None else len(r.data or [])}
    except Exception as exc:
        logger.warning("list_seller_reports failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}


@router.patch("/seller-reports/{report_id}/resolve")
def resolve_seller_report(report_id: str) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        admin.table("seller_reports").update({"resolved": True}).eq("id", report_id).execute()
        return {"status": "resolved"}
    except Exception as exc:
        logger.warning("resolve_seller_report failed: %s", exc)
        return {"error": "Failed to resolve seller report"}


@router.get("/trust-queue")
def list_trust_queue(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Thin admin queue: reports, near-dupes, and auto-moderation manual review."""
    admin = get_supabase_admin()
    product_reports: list[dict[str, Any]] = []
    seller_reports: list[dict[str, Any]] = []
    near_dupes: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    try:
        pr = (
            admin.table("product_reports")
            .select("id, product_id, reporter_id, reason, description, created_at, resolved")
            .eq("resolved", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        product_reports = enrich_product_reports(admin, pr.data or [])
    except Exception as exc:
        logger.warning("trust-queue product_reports failed: %s", exc)
    try:
        sr = (
            admin.table("seller_reports")
            .select("id, seller_id, reporter_id, reason, description, created_at, resolved")
            .eq("resolved", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        seller_reports = enrich_seller_reports(admin, sr.data or [])
    except Exception as exc:
        logger.warning("trust-queue seller_reports failed: %s", exc)
    try:
        mr = (
            admin.table("listing_moderation_queue")
            .select("id, product_id, seller_id, title, reason, status, created_at, scores")
            .eq("status", "needs_review")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        manual_review = mr.data or []
        near_dupes = [
            row for row in manual_review
            if "near_duplicate" in str(row.get("reason") or "").lower()
        ]
    except Exception as exc:
        logger.warning("trust-queue manual_review failed: %s", exc)
    return {
        "product_reports": product_reports,
        "seller_reports": seller_reports,
        "near_dupes": near_dupes,
        "manual_review": manual_review,
        "counts": {
            "product_reports": len(product_reports),
            "seller_reports": len(seller_reports),
            "near_dupes": len(near_dupes),
            "manual_review": len(manual_review),
        },
    }


@router.get("/near-duplicates")
def list_near_duplicates(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("listing_moderation_queue")
            .select("id, product_id, seller_id, title, reason, status, created_at, scores")
            .eq("status", "needs_review")
            .ilike("reason", "%near_duplicate%")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"items": r.data or [], "total": len(r.data or [])}
    except Exception as exc:
        logger.warning("list_near_duplicates failed: %s", exc, exc_info=True)
        return {"items": [], "total": 0}
