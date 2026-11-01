from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from db.supabase import Client

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_INV_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}
CHUNK = 150


def chunks(items: list[str], size: int = CHUNK) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def followed_shop_ids(client: Client, user_id: str | None) -> set[str]:
    if not user_id:
        return set()
    try:
        r = client.table("shop_follows").select("shop_id").eq("user_id", user_id).limit(500).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("followed_shop_ids failed: %s", exc)
        return set()
    return {str(row["shop_id"]) for row in (r.data or []) if row.get("shop_id")}


def collect_fraud_severity(
    client: Client,
    product_ids: list[str],
    shop_id_by_product: dict[str, str],
    shop_meta: dict[str, dict[str, Any]],
) -> dict[str, str]:
    if not product_ids:
        return {}
    per_product: dict[str, int] = {}
    try:
        for part in chunks(product_ids):
            r = (
                client.table("fraud_flags")
                .select("listing_id,severity,resolved")
                .in_("listing_id", part)
                .eq("resolved", False)
                .execute()
            )
            for row in r.data or []:
                pid = row.get("listing_id")
                if pid:
                    sev = _SEVERITY_RANK.get(str(row.get("severity")), 0)
                    per_product[str(pid)] = max(per_product.get(str(pid), -1), sev)
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect_fraud_severity(products) failed: %s", exc)

    per_owner: dict[str, int] = {}
    owner_ids = list({str(m.get("owner_id")) for m in shop_meta.values() if m.get("owner_id")})
    if owner_ids:
        try:
            for part in chunks(owner_ids):
                r = (
                    client.table("fraud_flags")
                    .select("seller_id,severity,resolved")
                    .in_("seller_id", part)
                    .eq("resolved", False)
                    .execute()
                )
                for row in r.data or []:
                    sid = str(row.get("seller_id"))
                    per_owner[sid] = max(per_owner.get(sid, -1), _SEVERITY_RANK.get(str(row.get("severity")), 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("collect_fraud_severity(sellers) failed: %s", exc)

    per_shop: dict[str, int] = {}
    for shop_id, meta in shop_meta.items():
        fs = float(meta.get("fraud_score") or 0)
        if fs >= 0.8:
            per_shop[shop_id] = _SEVERITY_RANK["critical"]
        elif fs >= 0.5:
            per_shop[shop_id] = _SEVERITY_RANK["high"]
        elif fs >= 0.25:
            per_shop[shop_id] = _SEVERITY_RANK["medium"]

    out: dict[str, str] = {}
    for pid in product_ids:
        rank = per_product.get(pid, -1)
        shop_id = shop_id_by_product.get(pid, "")
        if shop_id:
            owner = str(shop_meta.get(shop_id, {}).get("owner_id") or "")
            if owner:
                rank = max(rank, per_owner.get(owner, -1))
            rank = max(rank, per_shop.get(shop_id, -1))
        out[pid] = _INV_SEVERITY[rank] if rank >= 0 else "low"
    return out


def collect_shop_meta(client: Client, shop_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    try:
        for part in chunks(shop_ids):
            r = (
                client.table("shops")
                .select("id,owner_id,created_at,trust_score,seller_score,fraud_score,is_active,trust_badges")
                .in_("id", part)
                .execute()
            )
            out.update({str(s["id"]): s for s in (r.data or [])})
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect_shop_meta failed: %s", exc)
    return out
