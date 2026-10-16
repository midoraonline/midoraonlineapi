"""Admin-granted boosts (for testing): free, no plan allowance, recorded as source='admin'.

A business boost = one admin boost row on each of the shop's live listings, tagged with
shop_id so it can be listed and ended together. Listings posted later aren't covered.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

MAX_HOURS = 24 * 90
MAX_SCORE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(status: int, msg: str, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"detail": msg, "code": code})


def resolve_terms(admin: Any, package_id: str | None, hours: int | None, score: int | None) -> dict[str, Any]:
    """Package values, overridden by custom hours/score. Custom-only needs both."""
    pkg: dict[str, Any] | None = None
    if package_id:
        rows = admin.table("boost_plans").select("*").eq("id", package_id).limit(1).execute().data or []
        if not rows:
            raise _err(404, "Boost package not found.", "not_found")
        pkg = rows[0]
    h = hours if hours is not None else (int(pkg["duration_hours"]) if pkg else None)
    s = score if score is not None else (int(pkg.get("score_bonus") or 0) if pkg else None)
    if h is None or s is None:
        raise _err(422, "Pick a package or give both hours and score.", "invalid_boost")
    if not 1 <= int(h) <= MAX_HOURS:
        raise _err(422, f"Hours must be 1-{MAX_HOURS}.", "invalid_boost")
    if not 0 <= int(s) <= MAX_SCORE:
        raise _err(422, f"Score must be 0-{MAX_SCORE}.", "invalid_boost")
    return {"boost_plan_id": pkg["id"] if pkg else None, "hours": int(h), "score_bonus": int(s)}


def _row(listing_id: str, seller_id: str, terms: dict[str, Any], admin_id: str | None, shop_id: str | None) -> dict:
    now = _now()
    return {
        "listing_id": listing_id,
        "seller_id": seller_id,
        "boost_plan_id": terms["boost_plan_id"],
        "score_bonus": terms["score_bonus"],
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=terms["hours"])).isoformat(),
        "active": True,
        "payment_status": "completed",
        "payment_reference": "admin-grant",
        "source": "admin",
        "granted_by": admin_id,
        "shop_id": shop_id,
    }


def _end_active(admin: Any, listing_ids: list[str]) -> None:
    if listing_ids:
        admin.table("listing_boosts").update({"active": False, "ended_at": _now().isoformat()}).in_(
            "listing_id", listing_ids
        ).eq("active", True).execute()


def _refresh(listing_ids: list[str]) -> None:
    from feed.invalidate import after_rank_change

    after_rank_change(listing_ids)


def _owner(admin: Any, shop_id: str) -> dict[str, Any]:
    rows = admin.table("shops").select("id,name,owner_id").eq("id", shop_id).limit(1).execute().data or []
    if not rows:
        raise _err(404, "Business not found.", "not_found")
    return rows[0]


def boost_listing(admin: Any, listing_id: str, terms: dict[str, Any], admin_id: str | None) -> dict[str, Any]:
    rows = admin.table("products").select("id,shop_id,status").eq("id", listing_id).limit(1).execute().data or []
    if not rows:
        raise _err(404, "Listing not found.", "not_found")
    shop = _owner(admin, rows[0]["shop_id"])
    _end_active(admin, [listing_id])  # admin grant replaces any current boost
    created = admin.table("listing_boosts").insert(_row(listing_id, shop["owner_id"], terms, admin_id, None)).execute()
    _refresh([listing_id])
    return {"boosted": 1, "boost": (created.data or [None])[0]}


def boost_shop(admin: Any, shop_id: str, terms: dict[str, Any], admin_id: str | None) -> dict[str, Any]:
    shop = _owner(admin, shop_id)
    listings = (
        admin.table("products").select("id").eq("shop_id", shop_id).eq("status", "active")
        .eq("is_published", True).execute().data or []
    )
    ids = [str(p["id"]) for p in listings]
    if not ids:
        raise _err(409, "This business has no live listings to boost.", "no_listings")
    _end_active(admin, ids)
    admin.table("listing_boosts").insert([_row(i, shop["owner_id"], terms, admin_id, shop_id) for i in ids]).execute()
    _refresh(ids)
    return {"boosted": len(ids), "shop_id": shop_id}


def end_boost(admin: Any, boost_id: str) -> dict[str, Any]:
    r = admin.table("listing_boosts").update({"active": False, "ended_at": _now().isoformat()}).eq("id", boost_id).execute()
    if not r.data:
        raise _err(404, "Boost not found.", "not_found")
    _refresh([str(r.data[0]["listing_id"])])
    return {"ended": 1}


def end_listing_boosts(admin: Any, listing_id: str) -> dict[str, Any]:
    r = admin.table("listing_boosts").update({"active": False, "ended_at": _now().isoformat()}).eq(
        "listing_id", listing_id
    ).eq("active", True).execute()
    _refresh([listing_id])
    return {"ended": len(r.data or [])}


def end_shop_boosts(admin: Any, shop_id: str) -> dict[str, Any]:
    r = admin.table("listing_boosts").update({"active": False, "ended_at": _now().isoformat()}).eq(
        "shop_id", shop_id
    ).eq("active", True).execute()
    ids = [str(x["listing_id"]) for x in (r.data or [])]
    _refresh(ids)
    return {"ended": len(ids)}


def list_active(admin: Any, limit: int = 1000) -> list[dict[str, Any]]:
    rows = (
        admin.table("listing_boosts")
        .select("id,listing_id,seller_id,shop_id,score_bonus,starts_at,ends_at,source,granted_by,boost_plan_id,"
                "products(title,shop_id),boost_plans(name)")
        .eq("active", True)
        .gte("ends_at", _now().isoformat())
        .order("ends_at")
        .limit(limit)
        .execute()
        .data
        or []
    )
    shop_ids = {str((r.get("products") or {}).get("shop_id") or "") for r in rows} - {""}
    names: dict[str, str] = {}
    if shop_ids:
        for s in admin.table("shops").select("id,name").in_("id", list(shop_ids)).execute().data or []:
            names[str(s["id"])] = s.get("name") or ""
    out = []
    for r in rows:
        p = r.pop("products", None) or {}
        pk = r.pop("boost_plans", None) or {}
        sid = str(p.get("shop_id") or "")
        from shop.boost_info import SOURCE_LABEL

        src = r.get("source") or "paid"
        out.append({**r, "listing_title": p.get("title"), "listing_shop_id": sid or None,
                    "shop_name": names.get(sid), "package_name": pk.get("name") or "Custom",
                    "source_label": SOURCE_LABEL.get(src, src.title()),
                    "scope": "shop" if r.get("shop_id") else "listing",
                    "business_wide": bool(r.get("shop_id"))})
    return out
