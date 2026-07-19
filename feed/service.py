"""Midora feed orchestrator.

Public API:
    - `get_algorithm_feed(client, user_id, page, limit)` — personalized feed
    - `get_latest_feed(client, limit)`                   — anonymous fallback

The heavy lifting is delegated to:
    - `feed.signals`   → gather user / product signals
    - `feed.scoring`   → compute per-product score (pure function)
    - `feed.placement` → post-ranking composition & vendor-diversity rules

Every request re-scores the pool from live signals so interactions
(likes, views, follows) are reflected immediately. There is no cache.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any

from db.supabase import Client
from feed import config as C
from feed import impressions as imp
from feed import scoring as S
from feed import signals as sig
from feed.placement import rank_and_place
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

_PRODUCT_SELECT = (
    "id,shop_id,title,description,category,item_type,price_ugx,discount_price,"
    "discount_expires_at,stock_quantity,image_urls,is_published,status,listing_score,"
    "location_name,created_at,view_count,embedding,embedding_source_hash"
)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _active_products_query(client: Client) -> Any:
    return (
        client.table("products")
        .select(_PRODUCT_SELECT)
        .eq("status", "active")
        .eq("is_published", True)
    )


def _to_response(product: dict[str, Any]) -> ProductResponse:
    # ProductResponse schema doesn't need the raw embedding blob
    stripped = {
        k: v for k, v in product.items()
        if k not in ("embedding", "embedding_source_hash")
    }
    return ProductResponse(**stripped)


# ---------------------------------------------------------------------------
# Anonymous fallback
# ---------------------------------------------------------------------------

def get_latest_feed(client: Client, limit: int = 20) -> list[ProductResponse]:
    """Recency-sorted feed used when no personalization signals exist."""
    try:
        resp = (
            _active_products_query(client)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [_to_response(item) for item in resp.data or []]
    except Exception as exc:
        logger.warning("get_latest_feed failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------

def _fetch_candidates(
    client: Client,
    signals: dict[str, Any],
    pool_limit: int = C.CANDIDATE_POOL_MAX,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(rows: list[dict[str, Any]] | None) -> None:
        for row in rows or []:
            pid = str(row.get("id", ""))
            if pid and pid not in seen:
                seen.add(pid)
                out.append(row)

    # Top-scored active products (broad organic pool)
    try:
        r = (
            _active_products_query(client)
            .order("listing_score", desc=True)
            .order("created_at", desc=True)
            .limit(min(350, pool_limit))
            .execute()
        )
        _add(r.data)
    except Exception as exc:
        logger.warning("candidates(top-scored) failed: %s", exc)

    # Followed shops
    if signals["followed_shop_ids"]:
        try:
            r = (
                _active_products_query(client)
                .in_("shop_id", list(signals["followed_shop_ids"]))
                .order("listing_score", desc=True)
                .limit(200)
                .execute()
            )
            _add(r.data)
        except Exception as exc:
            logger.warning("candidates(followed) failed: %s", exc)

    # Preferred categories
    if signals["categories"]:
        try:
            r = (
                _active_products_query(client)
                .in_("category", list(signals["categories"]))
                .order("listing_score", desc=True)
                .limit(200)
                .execute()
            )
            _add(r.data)
        except Exception as exc:
            logger.warning("candidates(categories) failed: %s", exc)

    # Recent inventory — feeds fresh & new-seller pools
    try:
        r = (
            _active_products_query(client)
            .order("created_at", desc=True)
            .limit(pool_limit)
            .execute()
        )
        _add(r.data)
    except Exception as exc:
        logger.warning("candidates(recent) failed: %s", exc)

    # Exploration seed — pull products *outside* the user's known categories
    if signals["categories"]:
        try:
            r = (
                _active_products_query(client)
                .not_.in_("category", list(signals["categories"]))
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            _add(r.data)
        except Exception as exc:
            logger.warning("candidates(exploration seed) failed: %s", exc)

    return out[:pool_limit]


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def get_algorithm_feed(
    client: Client,
    user_id: str | None = None,
    page: int = 1,
    limit: int = 20,
    *,
    exclude_ids: list[str] | None = None,
    session_id: str | None = None,
) -> list[ProductResponse]:
    """Personalized feed with layered composition and vendor-diversity rules.

    Pipeline:
        1. Gather user signals (likes, views, follows, saves, WA, msg, search).
        2. Fetch candidate pool (top scored + followed + categories + recent
           + off-profile seeds for exploration).
        3. Fetch side-signal maps (velocity, fraud, shop meta, boost,
           per-shop 24h impressions).
        4. Build user preference vector from candidate embeddings.
        5. Filter candidates by exclude_ids + fatigue set.
        6. Score every candidate via `feed.scoring.score_product`
           (exposure multiplier applied inside).
        7. Placement engine reserves slots for boosted/sponsored/super-boost/
           premium/fresh/exploration and enforces the 12-window vendor rule.
        8. Return the requested page slice.
    """
    if not user_id and not session_id:
        return get_latest_feed(client, limit)

    # Pick up any admin overrides (cached to 60s in feed.config).
    try:
        C.refresh_from_db()
    except Exception:
        pass

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit

    # 1. User signals -----------------------------------------------------
    signals = sig.collect_user_signals(client, user_id) if user_id else sig.empty_signals()
    has_personal_signals = bool(
        signals["categories"]
        or signals["followed_shop_ids"]
        or signals["search_terms"]
        or signals["interactions"]
    )
    if not has_personal_signals and not exclude_ids:
        return get_latest_feed(client, limit)

    # 2. Candidate pool ---------------------------------------------------
    candidates = _fetch_candidates(client, signals)
    if not candidates:
        return get_latest_feed(client, limit)

    # 5a. Hard exclusion (client-tracked in-memory + server-side fatigue)
    exclude_set: set[str] = set(exclude_ids or [])
    if user_id or session_id:
        exclude_set |= imp.fatigued_listing_ids(
            buyer_id=user_id,
            session_id=session_id,
            threshold=C.FATIGUE_THRESHOLD,
            hours=C.FATIGUE_WINDOW_HOURS,
        )
    if exclude_set:
        candidates = [c for c in candidates if str(c.get("id", "")) not in exclude_set]

    if not candidates:
        return get_latest_feed(client, limit)

    product_ids = [str(p["id"]) for p in candidates if p.get("id")]
    shop_ids = list({str(p["shop_id"]) for p in candidates if p.get("shop_id")})

    # 3. Side-signal maps -------------------------------------------------
    shop_meta = sig.collect_shop_meta(client, shop_ids)
    velocity_map = sig.collect_velocity_map(client, product_ids)
    boost_map = sig.collect_boost_map(client, product_ids)
    shop_id_by_product = {
        str(p["id"]): str(p.get("shop_id", "")) for p in candidates if p.get("id")
    }
    fraud_map = sig.collect_fraud_severity(client, product_ids, shop_id_by_product, shop_meta)

    # Per-shop 24h impressions -> exposure multiplier
    shop_impressions = imp.shop_impressions_last_hours(shop_ids, hours=C.EXPOSURE_WINDOW_HOURS)
    exposure_multiplier = S.build_exposure_multiplier(shop_impressions)

    # 4. User preference vector ------------------------------------------
    embedding_map: dict[str, list[float]] = {}
    for c in candidates:
        pid = str(c.get("id", ""))
        emb = sig.parse_embedding(c.get("embedding"))
        if pid and emb:
            embedding_map[pid] = emb
    user_vector = sig.build_user_preference_vector(signals, embedding_map)

    logger.info(
        "feed: user=%s pool=%d shops=%d embeddings=%d vector=%s excluded=%d",
        user_id, len(candidates), len(shop_ids), len(embedding_map),
        "yes" if user_vector else "no", len(exclude_set),
    )

    # 6 + 7. Score + place ------------------------------------------------
    now = datetime.now(timezone.utc)
    score_fn = partial(
        S.score_product,
        signals=signals,
        user_vector=user_vector,
        shop_meta=shop_meta,
        velocity_map=velocity_map,
        boost_map=boost_map,
        fraud_map=fraud_map,
        exposure_multiplier=exposure_multiplier,
        now=now,
    )
    placement_cap = max(limit * (page + 1), 100)
    placed = rank_and_place(
        candidates,
        score_fn,
        boost_map=boost_map,
        shop_meta=shop_meta,
        signals=signals,
        limit=placement_cap,
    )

    # 8. Page slice -------------------------------------------------------
    page_items = placed[start_idx:end_idx]
    return [_to_response(item["product"]) for item in page_items]
