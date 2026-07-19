"""User + product signal collection for feed scoring.

Split out from `service.py` so scoring is a pure function of signals and
doesn't need to know how they were gathered.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from db.supabase import Client
from feed.config import (
    DECAY_LAMBDA_PER_DAY,
    INTERACTION_WEIGHTS,
    VELOCITY_WEIGHTS,
    VELOCITY_WINDOW_HOURS,
)
from feed.embeddings import parse_embedding, weighted_average

logger = logging.getLogger(__name__)


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


def time_decay(created_at: Any, now: datetime) -> float:
    ts = parse_timestamp(created_at)
    if ts is None:
        return 1.0
    days = max((now - ts).total_seconds() / 86400.0, 0.0)
    return math.exp(-DECAY_LAMBDA_PER_DAY * days)


# ---------------------------------------------------------------------------
# Per-user signals
# ---------------------------------------------------------------------------

def empty_signals() -> dict[str, Any]:
    return {
        "categories": set(),
        "search_terms": [],
        "liked_product_ids": set(),
        "viewed_product_ids": set(),
        "saved_product_ids": set(),
        "followed_shop_ids": set(),
        "interactions": [],
    }


def collect_user_signals(client: Client, user_id: str | None) -> dict[str, Any]:
    signals = empty_signals()
    if not user_id:
        return signals

    now = datetime.now(timezone.utc)

    def _add_categories(product_ids: list[str]) -> None:
        if not product_ids:
            return
        try:
            cat_resp = (
                client.table("products")
                .select("category")
                .in_("id", product_ids)
                .execute()
            )
            for p in cat_resp.data or []:
                if p.get("category"):
                    signals["categories"].add(p["category"])
        except Exception:
            pass

    def _pull_events(event_type: str, weight_key: str) -> list[str]:
        try:
            resp = (
                client.table("listing_events")
                .select("listing_id,created_at")
                .eq("buyer_id", user_id)
                .eq("event_type", event_type)
                .order("created_at", desc=True)
                .limit(30)
                .execute()
            )
        except Exception as exc:
            logger.warning("collect_user_signals(%s) failed: %s", event_type, exc)
            return []
        ids: list[str] = []
        for item in resp.data or []:
            pid = str(item["listing_id"])
            ids.append(pid)
            signals["interactions"].append({
                "product_id": pid,
                "type": weight_key,
                "weight": INTERACTION_WEIGHTS[weight_key] * time_decay(item.get("created_at"), now),
            })
        return ids

    # --- Likes ---
    try:
        likes_resp = (
            client.table("product_likes")
            .select("product_id,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        liked_ids: list[str] = []
        for item in likes_resp.data or []:
            pid = str(item["product_id"])
            liked_ids.append(pid)
            signals["liked_product_ids"].add(pid)
            signals["interactions"].append({
                "product_id": pid,
                "type": "like",
                "weight": INTERACTION_WEIGHTS["like"] * time_decay(item.get("created_at"), now),
            })
        _add_categories(liked_ids)
    except Exception as exc:
        logger.warning("collect_user_signals(likes) failed: %s", exc)

    viewed_ids = _pull_events("viewed", "view")
    signals["viewed_product_ids"].update(viewed_ids)
    _add_categories(viewed_ids)

    saved_ids = _pull_events("saved", "save")
    signals["saved_product_ids"].update(saved_ids)
    _add_categories(saved_ids)

    _pull_events("whatsapp_clicked", "whatsapp")
    _pull_events("messaged", "message")

    # --- Followed shops ---
    try:
        follow_resp = (
            client.table("shop_follows")
            .select("shop_id")
            .eq("user_id", user_id)
            .limit(200)
            .execute()
        )
        for row in follow_resp.data or []:
            signals["followed_shop_ids"].add(str(row["shop_id"]))
    except Exception as exc:
        logger.warning("collect_user_signals(follows) failed: %s", exc)

    # --- Search history ---
    try:
        search_resp = (
            client.table("search_history")
            .select("query,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(8)
            .execute()
        )
        seen: set[str] = set()
        for s in search_resp.data or []:
            q = (s.get("query") or "").strip().lower()
            if not q or q in seen:
                continue
            seen.add(q)
            signals["search_terms"].append(q)
    except Exception as exc:
        logger.warning("collect_user_signals(search) failed: %s", exc)

    return signals


def build_user_preference_vector(
    signals: dict[str, Any],
    embeddings_map: dict[str, list[float]],
) -> list[float] | None:
    weighted: list[tuple[list[float], float]] = []
    for interaction in signals["interactions"]:
        pid = interaction.get("product_id")
        if not pid or pid == "None":
            continue
        emb = embeddings_map.get(pid)
        if not emb:
            continue
        w = float(interaction.get("weight") or 0.0)
        if w > 0:
            weighted.append((emb, w))
    return weighted_average(weighted)


# ---------------------------------------------------------------------------
# Product-side signals (velocity + fraud + new seller)
# ---------------------------------------------------------------------------

def collect_velocity_map(client: Client, product_ids: list[str]) -> dict[str, float]:
    """Sum of weighted engagement events per product inside the velocity window.

    Returns raw weighted totals — the score curve is applied in scoring.
    """
    if not product_ids:
        return {}
    since = (datetime.now(timezone.utc)).timestamp() - VELOCITY_WINDOW_HOURS * 3600
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
    out: dict[str, float] = {}
    try:
        resp = (
            client.table("listing_events")
            .select("listing_id,event_type")
            .in_("listing_id", product_ids)
            .gte("created_at", since_iso)
            .limit(5000)
            .execute()
        )
        for row in resp.data or []:
            pid = str(row.get("listing_id"))
            etype = str(row.get("event_type"))
            w = VELOCITY_WEIGHTS.get(etype, 0.0)
            if pid and w > 0:
                out[pid] = out.get(pid, 0.0) + w
    except Exception as exc:
        logger.warning("collect_velocity_map failed: %s", exc)
    # Fold in recent likes as velocity too
    try:
        resp = (
            client.table("product_likes")
            .select("product_id")
            .in_("product_id", product_ids)
            .gte("created_at", since_iso)
            .limit(5000)
            .execute()
        )
        w = VELOCITY_WEIGHTS.get("like", 0.0)
        for row in resp.data or []:
            pid = str(row.get("product_id"))
            if pid and w > 0:
                out[pid] = out.get(pid, 0.0) + w
    except Exception as exc:
        logger.warning("collect_velocity_map(likes) failed: %s", exc)
    return out


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_INV_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}


def collect_fraud_severity(
    client: Client,
    product_ids: list[str],
    shop_id_by_product: dict[str, str],
    shop_meta: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Return highest unresolved fraud severity per product.

    Combines product-level flags, seller-level flags (via shop owner_id),
    and shops.fraud_score (0..1) escalation.
    """
    if not product_ids:
        return {}

    per_product: dict[str, int] = {}

    # Product-level flags
    try:
        r = (
            client.table("fraud_flags")
            .select("listing_id,severity,resolved")
            .in_("listing_id", product_ids)
            .eq("resolved", False)
            .execute()
        )
        for row in r.data or []:
            sev = _SEVERITY_RANK.get(str(row.get("severity")), 0)
            pid = row.get("listing_id")
            if pid:
                per_product[str(pid)] = max(per_product.get(str(pid), -1), sev)
    except Exception as exc:
        logger.warning("collect_fraud_severity(products) failed: %s", exc)

    # Seller-level flags (aggregate across owner_id)
    per_owner: dict[str, int] = {}
    owner_ids = list({
        str(meta.get("owner_id", ""))
        for meta in shop_meta.values()
        if meta.get("owner_id")
    })
    try:
        if owner_ids:
            r = (
                client.table("fraud_flags")
                .select("seller_id,severity,resolved")
                .in_("seller_id", owner_ids)
                .eq("resolved", False)
                .execute()
            )
            for row in r.data or []:
                sev = _SEVERITY_RANK.get(str(row.get("severity")), 0)
                sid = str(row.get("seller_id"))
                per_owner[sid] = max(per_owner.get(sid, -1), sev)
    except Exception as exc:
        logger.warning("collect_fraud_severity(sellers) failed: %s", exc)

    # Escalate via shops.fraud_score (0..1)
    per_shop_score: dict[str, int] = {}
    for shop_id, meta in shop_meta.items():
        fs = float(meta.get("fraud_score") or 0)
        rank = -1
        if fs >= 0.8:
            rank = _SEVERITY_RANK["critical"]
        elif fs >= 0.5:
            rank = _SEVERITY_RANK["high"]
        elif fs >= 0.25:
            rank = _SEVERITY_RANK["medium"]
        if rank >= 0:
            per_shop_score[shop_id] = rank

    out: dict[str, str] = {}
    for pid in product_ids:
        rank = per_product.get(pid, -1)
        shop_id = shop_id_by_product.get(pid, "")
        if shop_id:
            meta = shop_meta.get(shop_id, {})
            owner = str(meta.get("owner_id", ""))
            if owner:
                rank = max(rank, per_owner.get(owner, -1))
            rank = max(rank, per_shop_score.get(shop_id, -1))
        out[pid] = _INV_SEVERITY[rank] if rank >= 0 else "low"
    return out


def collect_shop_meta(client: Client, shop_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch shop attributes needed for scoring & placement."""
    if not shop_ids:
        return {}
    try:
        r = (
            client.table("shops")
            .select(
                "id,owner_id,created_at,subscription_end_date,trust_score,seller_score,"
                "fraud_score,is_active,available_now,trust_badges"
            )
            .in_("id", shop_ids)
            .execute()
        )
    except Exception as exc:
        logger.warning("collect_shop_meta failed: %s", exc)
        return {}
    return {str(s["id"]): s for s in (r.data or [])}


def collect_boost_map(client: Client, product_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return active boost record per product (with plan metadata)."""
    if not product_ids:
        return {}
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        r = (
            client.table("listing_boosts")
            .select("listing_id,score_bonus,boost_plan_id,ends_at")
            .in_("listing_id", product_ids)
            .eq("active", True)
            .gte("ends_at", now_iso)
            .execute()
        )
    except Exception as exc:
        logger.warning("collect_boost_map failed: %s", exc)
        return {}
    return {str(row["listing_id"]): row for row in (r.data or []) if row.get("listing_id")}


# Re-export for convenience
__all__ = [
    "parse_timestamp",
    "time_decay",
    "empty_signals",
    "collect_user_signals",
    "build_user_preference_vector",
    "collect_velocity_map",
    "collect_fraud_severity",
    "collect_shop_meta",
    "collect_boost_map",
    "parse_embedding",
]
