"""Incremental feed re-scoring.

The old model fully recomputed a user's personalized ranking every time
their cache expired (1h TTL) or on cold start — a heavy candidate-pool
fetch + scoring pass even when nothing the user cares about had changed.

Now the base ranking persists in Redis indefinitely (see
feed/service.py:_load_cached_ranked_ids / _save_cached_ranked_ids) and we
only touch the handful of items actually affected by a signal change —
a follow, a like, a boost grant — recomputing just those scores and
repositioning them in the already-cached list. A page reload is then just
a cheap read + slice, never a full re-rank.

Every entry point here is best-effort: if Redis is down or the user has no
cache yet, we simply do nothing — the next cold-start full compute will
pick up the new signal naturally.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.cache import cache_get_json, cache_set_json, get_redis
from feed import scoring as S
from feed import signals as sig
from feed.embeddings import cosine_similarity, parse_embedding

logger = logging.getLogger(__name__)

_PRODUCT_FIELDS = (
    "id,shop_id,title,category,item_type,price_ugx,stock_quantity,"
    "is_published,status,listing_score,location_name,created_at,view_count"
)


def _cache_key(user_id: str) -> str:
    return f"feed:user:{user_id}"


def _load_payload(user_id: str) -> dict[str, Any] | None:
    if get_redis() is None:
        return None
    payload = cache_get_json(_cache_key(user_id))
    if not payload or "scores" not in payload or not payload.get("ranked_ids"):
        return None
    return payload


def _save_payload(user_id: str, payload: dict[str, Any]) -> None:
    # Same long "abandoned account" safety-net TTL as the full compute path.
    from feed.service import FEED_CACHE_PERSIST_TTL_SECONDS

    cache_set_json(_cache_key(user_id), payload, ttl_seconds=FEED_CACHE_PERSIST_TTL_SECONDS)


def _rescore_ids(user_id: str, product_ids: list[str]) -> None:
    """Recompute scores for specific products already in a user's cached feed
    and reposition them — never touches anything else in the ranked list."""
    product_ids = [str(p) for p in dict.fromkeys(product_ids) if p]
    if not product_ids:
        return
    payload = _load_payload(user_id)
    if payload is None:
        return  # no cache yet; the next full compute will include this signal

    ranked_ids: list[str] = payload.get("ranked_ids") or []
    scores: dict[str, float] = dict(payload.get("scores") or {})
    targets = [pid for pid in product_ids if pid in ranked_ids]
    if not targets:
        return  # item isn't in this user's feed right now — nothing to slide

    try:
        from db.supabase import get_supabase_admin

        db = get_supabase_admin()

        rows = (
            db.table("products")
            .select(_PRODUCT_FIELDS)
            .in_("id", targets)
            .execute()
        )
        products = {str(r["id"]): r for r in (rows.data or []) if r.get("id")}
        if not products:
            return

        shop_ids = list({str(p["shop_id"]) for p in products.values() if p.get("shop_id")})
        shop_id_by_product = {pid: str(p.get("shop_id", "")) for pid, p in products.items()}

        user_signals = sig.collect_user_signals(db, user_id)
        shop_meta = sig.collect_shop_meta(db, shop_ids)
        velocity_map = sig.collect_velocity_map(db, targets)
        boost_map = sig.collect_boost_map(db, targets)
        fraud_map = sig.collect_fraud_severity(db, targets, shop_id_by_product, shop_meta)

        user_vector = parse_embedding(payload.get("preference_vector"))
        taste_scores: dict[str, float] = {}
        if user_vector:
            embeddings = {}
            try:
                emb_rows = (
                    db.table("products")
                    .select("id,embedding")
                    .in_("id", targets)
                    .execute()
                )
                for row in emb_rows.data or []:
                    vec = parse_embedding(row.get("embedding"))
                    if vec:
                        embeddings[str(row["id"])] = vec
            except Exception as exc:
                logger.info("incremental rescore: embedding fetch skipped: %s", exc)
            for pid, vec in embeddings.items():
                taste_scores[pid] = max(cosine_similarity(user_vector, vec), 0.0)

        now = datetime.now(timezone.utc)
        for pid in targets:
            product = products.get(pid)
            if not product:
                continue
            new_score = S.score_product(
                product,
                signals=user_signals,
                user_vector=user_vector,
                shop_meta=shop_meta,
                velocity_map=velocity_map,
                boost_map=boost_map,
                fraud_map=fraud_map,
                taste_scores=taste_scores,
                now=now,
            )
            if new_score is None:
                # Now critical-fraud-hidden: drop it instead of leaving a stale score.
                scores.pop(pid, None)
                if pid in ranked_ids:
                    ranked_ids.remove(pid)
                continue
            scores[pid] = round(float(new_score), 4)

        ranked_ids = [pid for pid in ranked_ids if pid in scores]
        ranked_ids.sort(key=lambda pid: scores.get(pid, 0.0), reverse=True)

        payload["ranked_ids"] = ranked_ids
        payload["scores"] = scores
        _save_payload(user_id, payload)
        logger.info(
            "feed-incremental: rescored user=%s items=%s (slide, no full recompute)",
            user_id, len(targets),
        )
    except Exception as exc:  # noqa: BLE001 — a failed slide must never break the triggering action
        logger.warning("feed-incremental: rescore failed for user=%s: %s", user_id, exc)


def on_product_signal_changed(user_id: str | None, product_id: str | None) -> None:
    """A single product's standing for this user changed (like/unlike, view)."""
    if not user_id or not product_id:
        return
    _rescore_ids(user_id, [product_id])


def on_shop_follow_changed(user_id: str | None, shop_id: str | None) -> None:
    """Follow/unfollow a shop — reposition that shop's products already in the
    user's cached feed (Followed Shop Bonus just appeared/disappeared)."""
    if not user_id or not shop_id:
        return
    payload = _load_payload(user_id)
    if payload is None:
        return
    ranked_ids = set(payload.get("ranked_ids") or [])
    if not ranked_ids:
        return
    try:
        from db.supabase import get_supabase_admin

        rows = (
            get_supabase_admin()
            .table("products")
            .select("id")
            .eq("shop_id", shop_id)
            .in_("id", list(ranked_ids))
            .execute()
        )
        shop_product_ids = [str(r["id"]) for r in (rows.data or []) if r.get("id")]
    except Exception as exc:
        logger.info("feed-incremental: shop product lookup skipped: %s", exc)
        return
    if shop_product_ids:
        _rescore_ids(user_id, shop_product_ids)
