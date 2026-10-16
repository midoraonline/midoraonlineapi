"""Describe active boosts: which package (or Custom), source, scope and end time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SOURCE_LABEL = {"admin": "Admin", "plan_allowance": "Plan", "paid": "Paid"}
_COLS = "id,listing_id,shop_id,score_bonus,starts_at,ends_at,source,boost_plan_id,boost_plans(name)"


def describe(row: dict[str, Any]) -> dict[str, Any]:
    pkg = (row.get("boost_plans") or {}).get("name") if isinstance(row.get("boost_plans"), dict) else None
    source = row.get("source") or "paid"
    return {
        "id": row.get("id"),
        "listing_id": str(row.get("listing_id")) if row.get("listing_id") else None,
        "package_name": pkg or "Custom",
        "source": source,
        "source_label": SOURCE_LABEL.get(source, source.title()),
        "scope": "shop" if row.get("shop_id") else "listing",
        "shop_id": row.get("shop_id"),
        "score_bonus": int(row.get("score_bonus") or 0),
        "starts_at": row.get("starts_at"),
        "ends_at": row.get("ends_at"),
    }


def _select(client: Any, cols: str):
    return client.table("listing_boosts").select(cols)


def active_boost_map(client: Any, listing_ids: list[str]) -> dict[str, dict[str, Any]]:
    """listing_id -> describe(latest-ending active boost). Falls back without 059 columns."""
    ids = [str(i) for i in listing_ids if i]
    if not ids:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for cols in (_COLS, _COLS.replace("shop_id,", "")):
        try:
            rows = (
                _select(client, cols).in_("listing_id", ids).eq("active", True).gte("ends_at", now)
                .order("ends_at", desc=True).execute().data or []
            )
            break
        except Exception:  # noqa: BLE001
            rows = []
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        lid = str(r.get("listing_id") or "")
        if lid and lid not in out:
            out[lid] = describe(r)
    return out


def card_fields(info: dict[str, Any] | None) -> dict[str, Any]:
    """Fields added to feed cards / product detail."""
    return {
        "boosted": info is not None,
        "is_boosted": info is not None,
        "boost_ends_at": info.get("ends_at") if info else None,
        "boost": info,
    }
