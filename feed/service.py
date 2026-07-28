"""Midora feed orchestrator.

Public API:
    - `get_algorithm_feed(client, user_id, page, limit)` — personalized feed
    - `get_latest_feed(client, limit)`                   — anonymous fallback

The heavy lifting is delegated to:
    - `feed.signals`   → gather user / product signals
    - `feed.scoring`   → compute per-product score (pure function)
    - `feed.placement` → post-ranking composition & vendor-diversity rules

Authenticated users are cached for one hour in `user_feed_cache` as an
ordered ID list (+ optional preference vector). Request-time exclusions /
fatigue filter that ID list; only the page slice is hydrated into card rows.

Cold rebuild path:
    1. Build user preference vector from interaction product embeddings.
    2. ANN / cosine match via pgvector RPC when available.
    3. Merge with lean candidate pools (no embedding blobs).
    4. Score + place once; persist ranked IDs.
    5. Hydrate card fields for the requested page only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from functools import partial
from typing import Any

from db.supabase import Client
from feed import config as C
from feed import impressions as imp
from feed import scoring as S
from feed import signals as sig
from feed.embeddings import cosine_similarity, parse_embedding
from feed.placement import rank_and_place
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)
FEED_CACHE_TTL = timedelta(hours=1)
FEED_CACHE_TTL_SECONDS = int(FEED_CACHE_TTL.total_seconds())
FEED_CACHE_MAX_IDS = 800
VECTOR_MATCH_COUNT = 220

# Card payload — keep lean for list/feed responses (no description / embedding).
_PRODUCT_CARD_SELECT = (
    "id,shop_id,title,category,item_type,price_ugx,discount_price,"
    "discount_expires_at,stock_quantity,image_urls,is_published,status,listing_score,"
    "location_name,created_at,view_count"
)

# Scoring candidates: card fields only. Taste comes from pgvector / taste_scores.
_PRODUCT_SCORE_SELECT = _PRODUCT_CARD_SELECT


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _active_products_query(client: Client) -> Any:
    """Candidate / score queries — lean columns, no vector payload."""
    return (
        client.table("products")
        .select(_PRODUCT_SCORE_SELECT)
        .eq("status", "active")
        .eq("is_published", True)
    )


def _lean_active_products_query(client: Client) -> Any:
    """List/feed queries without embedding columns."""
    return (
        client.table("products")
        .select(_PRODUCT_CARD_SELECT)
        .eq("status", "active")
        .eq("is_published", True)
    )


def _to_response(product: dict[str, Any]) -> ProductResponse:
    stripped = {
        k: v for k, v in product.items()
        if k not in ("embedding", "embedding_source_hash", "embedding_vec", "similarity")
    }
    stripped.setdefault("description", None)
    return ProductResponse(**stripped)


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        text = str(raw)
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _cache_is_fresh(refreshed_at: Any) -> bool:
    """True while within the 1-hour ranking TTL (no full re-score yet)."""
    ts = _parse_ts(refreshed_at)
    if not ts:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return age <= FEED_CACHE_TTL


def _cache_age_seconds(refreshed_at: Any) -> int | None:
    ts = _parse_ts(refreshed_at)
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))


def _load_cached_ranked_ids(
    client: Client,
    user_id: str,
) -> tuple[list[str], bool, datetime | None]:
    """Return (ranked_ids, is_fresh, refreshed_at) for a user's cached feed."""
    try:
        row = (
            client.table("user_feed_cache")
            .select("ranked_ids, refreshed_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not row.data:
            logger.info("feed-cache: miss user=%s (no row)", user_id)
            return [], False, None
        item = row.data[0] or {}
        raw_ids = item.get("ranked_ids") or []
        ids = [str(x) for x in raw_ids if x][:FEED_CACHE_MAX_IDS]
        refreshed_at = _parse_ts(item.get("refreshed_at"))
        fresh = _cache_is_fresh(item.get("refreshed_at"))
        age = _cache_age_seconds(item.get("refreshed_at"))
        logger.info(
            "feed-cache: hit user=%s ids=%s fresh=%s age_s=%s ttl_s=%s",
            user_id, len(ids), fresh, age, FEED_CACHE_TTL_SECONDS,
        )
        return ids, fresh, refreshed_at
    except Exception as exc:
        logger.warning(
            "feed-cache read failed for user=%s (apply 029_user_feed_cache.sql?): %s",
            user_id, exc,
        )
        return [], False, None


def _save_cached_ranked_ids(
    client: Client,
    user_id: str,
    ranked_ids: list[str],
    *,
    preference_vector: list[float] | None = None,
    candidate_count: int = 0,
) -> None:
    if not ranked_ids:
        return
    payload: dict[str, Any] = {
        "user_id": user_id,
        "ranked_ids": ranked_ids[:FEED_CACHE_MAX_IDS],
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": int(candidate_count or len(ranked_ids)),
    }
    if preference_vector:
        payload["preference_vector"] = [round(float(v), 6) for v in preference_vector]
    try:
        client.table("user_feed_cache").upsert(
            payload,
            on_conflict="user_id",
        ).execute()
        logger.info(
            "feed-cache: saved user=%s ranked=%s vector=%s",
            user_id, len(ranked_ids), "yes" if preference_vector else "no",
        )
    except Exception as exc:
        try:
            client.table("user_feed_cache").upsert(
                {
                    "user_id": user_id,
                    "ranked_ids": ranked_ids[:FEED_CACHE_MAX_IDS],
                    "refreshed_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="user_id",
            ).execute()
            logger.info(
                "feed-cache: saved(basic) user=%s ranked=%s",
                user_id, len(ranked_ids),
            )
        except Exception as exc2:
            logger.warning(
                "feed-cache write failed for user=%s (apply 029/032?): %s / %s",
                user_id, exc, exc2,
            )


def _fetch_products_by_ids(client: Client, product_ids: list[str]) -> list[dict[str, Any]]:
    """Hydrate card rows for a page of ranked IDs (preserves order)."""
    if not product_ids:
        return []
    from db.supabase import get_supabase_admin, with_supabase_retry

    def _run():
        db = get_supabase_admin()
        return (
            _lean_active_products_query(db)
            .in_("id", product_ids)
            .execute()
        )

    try:
        r = with_supabase_retry(_run, label="hydrate_cards")
        rows = r.data or []
        by_id = {str(row.get("id")): row for row in rows if row.get("id")}
        return [by_id[pid] for pid in product_ids if pid in by_id]
    except Exception as exc:
        logger.warning("feed-cache product fetch failed: %s", exc)
        return []


def _apply_runtime_exclusions(
    product_ids: list[str],
    *,
    user_id: str | None,
    session_id: str | None,
    exclude_ids: list[str] | None,
    apply_fatigue: bool = True,
) -> list[str]:
    """Filter a pre-ranked ID list without re-scoring."""
    exclude_set: set[str] = set(exclude_ids or [])
    if apply_fatigue and (user_id or session_id):
        try:
            exclude_set |= imp.fatigued_listing_ids(
                buyer_id=user_id,
                session_id=session_id,
                threshold=C.FATIGUE_THRESHOLD,
                hours=C.FATIGUE_WINDOW_HOURS,
            )
        except Exception as exc:
            logger.warning("feed-cache fatigue lookup failed: %s", exc)
    if not exclude_set:
        return product_ids
    return [pid for pid in product_ids if pid not in exclude_set]


def _page_ids_from_ranked(
    ranked_ids: list[str],
    *,
    page: int,
    limit: int,
    exclude_ids: list[str] | None,
    user_id: str | None,
    session_id: str | None,
) -> list[str]:
    """Slice the ranked ID list for this request.

    Contract:
      - Initial / cursor pagination (`exclude_ids` empty): page window into the
        full ranked list. No session exclusions on first paint.
      - Load-more continuation (`exclude_ids` set): drop already-shown IDs, then
        return the next `limit` from the head of what remains. Page offset is
        ignored so we never double-skip (exclude + page 2).
      - Fatigue only applies when continuing past the first screen.
    """
    using_exclude = bool(exclude_ids)
    apply_fatigue = using_exclude or page > 1
    filtered = _apply_runtime_exclusions(
        ranked_ids,
        user_id=user_id,
        session_id=session_id,
        exclude_ids=exclude_ids,
        apply_fatigue=apply_fatigue,
    )
    if using_exclude:
        return filtered[:limit]
    start_idx = max(0, (page - 1) * limit)
    return filtered[start_idx : start_idx + limit]


def _merge_recent_unseen_ids(
    client: Client,
    ranked_ids: list[str],
    refreshed_at: datetime | None,
) -> list[str]:
    """Prepend newly created listings missing from a fresh cache."""
    if not ranked_ids:
        return ranked_ids
    if refreshed_at and refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    try:
        q = (
            client.table("products")
            .select("id")
            .eq("status", "active")
            .eq("is_published", True)
            .order("created_at", desc=True)
            .limit(180)
        )
        if refreshed_at:
            q = q.gte("created_at", refreshed_at.isoformat())
        r = q.execute()
        recent_ids = [
            str(row.get("id"))
            for row in (r.data or [])
            if row.get("id")
        ]
    except Exception as exc:
        logger.warning("feed-cache recent listings fetch failed: %s", exc)
        return ranked_ids

    seen = set(ranked_ids)
    unseen_recent = [pid for pid in recent_ids if pid not in seen]
    if not unseen_recent:
        return ranked_ids
    return (unseen_recent + ranked_ids)[:FEED_CACHE_MAX_IDS]


def invalidate_user_feed_cache(client: Any, user_id: str | None) -> None:
    """No-op for routine engagement.

    Personalized ranking is recalculated on a fixed **1-hour TTL**
    (`FEED_CACHE_TTL`). Likes / follows / search must not wipe the cache on
    every action — that forced constant re-scores and looked like “latest”.

    Fresh inventory still surfaces within the TTL via `_merge_recent_unseen_ids`.
    Pass `force=True` only from rare admin/debug paths.
    """
    return


def invalidate_user_feed_cache_now(client: Any, user_id: str | None) -> None:
    """Hard-drop cached ranking (admin / explicit rebuild only)."""
    if not user_id:
        return
    try:
        from db.supabase import get_supabase_admin

        get_supabase_admin().table("user_feed_cache").delete().eq("user_id", user_id).execute()
        logger.info("feed-cache: forced invalidate user=%s", user_id)
    except Exception as exc:
        logger.warning("feed-cache invalidate failed for user=%s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Anonymous fallback
# ---------------------------------------------------------------------------

def get_latest_feed(client: Client, limit: int = 20) -> list[ProductResponse]:
    """Recency-sorted feed used when no personalization signals exist."""
    from db.supabase import get_supabase_admin, with_supabase_retry

    def _run():
        db = get_supabase_admin()
        return (
            _lean_active_products_query(db)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    try:
        resp = with_supabase_retry(_run, label="get_latest_feed")
        return [_to_response(item) for item in resp.data or []]
    except Exception as exc:
        logger.warning("get_latest_feed failed: %s", exc)
        return []


def _get_latest_feed_page(
    client: Client,
    *,
    page: int,
    limit: int,
) -> list[ProductResponse]:
    from db.supabase import get_supabase_admin, with_supabase_retry

    start_idx = max(0, (page - 1) * limit)
    end_idx = start_idx + limit - 1

    def _run():
        db = get_supabase_admin()
        return (
            _lean_active_products_query(db)
            .order("created_at", desc=True)
            .range(start_idx, end_idx)
            .execute()
        )

    try:
        resp = with_supabase_retry(_run, label="latest_feed_page")
        return [_to_response(item) for item in resp.data or []]
    except Exception as exc:
        logger.warning("_get_latest_feed_page failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Vector preference + ANN
# ---------------------------------------------------------------------------

def _interaction_product_ids(signals: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for interaction in signals.get("interactions") or []:
        pid = str(interaction.get("product_id") or "")
        if pid and pid not in seen and pid != "None":
            seen.add(pid)
            ids.append(pid)
    for key in ("liked_product_ids", "viewed_product_ids", "saved_product_ids"):
        for pid in signals.get(key) or []:
            s = str(pid)
            if s and s not in seen:
                seen.add(s)
                ids.append(s)
    return ids[:80]


def _load_embeddings_for_ids(
    client: Client,
    product_ids: list[str],
) -> dict[str, list[float]]:
    """Fetch JSONB embeddings only for the small interaction set."""
    if not product_ids:
        return {}
    out: dict[str, list[float]] = {}
    chunk = 80
    for i in range(0, len(product_ids), chunk):
        subset = product_ids[i : i + chunk]
        try:
            r = (
                client.table("products")
                .select("id,embedding")
                .in_("id", subset)
                .execute()
            )
        except Exception as exc:
            logger.warning("interaction embedding fetch failed: %s", exc)
            continue
        for row in r.data or []:
            pid = str(row.get("id") or "")
            emb = parse_embedding(row.get("embedding"))
            if pid and emb:
                out[pid] = emb
    return out


def _match_products_by_vector(
    client: Client,
    user_vector: list[float],
    *,
    match_count: int = VECTOR_MATCH_COUNT,
    exclude_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """ANN cosine match via pgvector RPC. Returns (lean rows, taste_scores)."""
    if not user_vector:
        return [], {}
    try:
        payload = {
            "query_embedding": "[" + ",".join(str(float(v)) for v in user_vector) + "]",
            "match_count": int(match_count),
            "exclude_ids": [pid for pid in (exclude_ids or []) if pid][:400],
        }
        r = client.rpc("match_feed_products", payload).execute()
    except Exception as exc:
        logger.info("match_feed_products RPC unavailable (apply 032): %s", exc)
        return [], {}

    rows: list[dict[str, Any]] = []
    taste: dict[str, float] = {}
    for row in r.data or []:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        sim = float(row.get("similarity") or 0.0)
        taste[pid] = max(0.0, min(sim, 1.0))
        rows.append({
            "id": pid,
            "shop_id": str(row.get("shop_id") or ""),
            "category": row.get("category"),
            "listing_score": int(row.get("listing_score") or 0),
            "created_at": row.get("created_at"),
            "view_count": int(row.get("view_count") or 0),
            "title": "",
            "price_ugx": 0,
            "image_urls": [],
            "is_published": True,
            "status": "active",
        })
    return rows, taste


def _hydrate_candidate_details(
    client: Client,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill lean score fields for candidates that only have id/shop from ANN."""
    need = [
        str(c.get("id"))
        for c in candidates
        if c.get("id") and not c.get("title")
    ]
    seen_order: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        pid = str(c.get("id") or "")
        if pid and pid not in seen:
            seen.add(pid)
            seen_order.append(pid)

    by_id: dict[str, dict[str, Any]] = {
        str(c.get("id")): c for c in candidates if c.get("id") and c.get("title")
    }
    if need:
        chunk = 100
        for i in range(0, len(need), chunk):
            subset = need[i : i + chunk]
            try:
                r = _active_products_query(client).in_("id", subset).execute()
            except Exception as exc:
                logger.warning("candidate hydrate failed: %s", exc)
                continue
            for row in r.data or []:
                pid = str(row.get("id") or "")
                if pid:
                    by_id[pid] = row

    return [by_id[pid] for pid in seen_order if pid in by_id]


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

    try:
        r = (
            _active_products_query(client)
            .order("created_at", desc=True)
            .limit(min(200, pool_limit))
            .execute()
        )
        _add(r.data)
    except Exception as exc:
        logger.warning("candidates(recent) failed: %s", exc)

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

    Ranking lifecycle (per authenticated user):
      * Cold / TTL expired (`refreshed_at` older than 1 hour): full score + place,
        then upsert `user_feed_cache` with new ranked IDs.
      * Hot (within 1 hour): serve cached ranked IDs → apply load-more exclusions
        → hydrate card columns only. No re-score.
      * Within TTL, newly created listings are merged via `_merge_recent_unseen_ids`.

    Authenticated users never short-circuit to pure latest solely because they
    lack likes/follows yet — we still score organic inventory and cache it.
    """
    # Feed ranking + cache must use service role (table grants + RLS).
    from db.supabase import get_supabase_admin

    db = get_supabase_admin()

    if not user_id:
        logger.info("feed:path=guest_latest page=%s limit=%s", page, limit)
        return _get_latest_feed_page(db, page=page, limit=limit)

    try:
        C.refresh_from_db()
    except Exception:
        pass

    cached_ids, is_fresh, refreshed_at = _load_cached_ranked_ids(db, user_id)
    if cached_ids and is_fresh:
        cached_ids = _merge_recent_unseen_ids(db, cached_ids, refreshed_at)
        page_ids = _page_ids_from_ranked(
            cached_ids,
            page=page,
            limit=limit,
            exclude_ids=exclude_ids,
            user_id=user_id,
            session_id=session_id,
        )
        cached_rows = _fetch_products_by_ids(db, page_ids)
        if cached_rows:
            logger.info(
                "feed:path=cache_hit user=%s page=%s n=%s cached=%s ttl_s=%s",
                user_id, page, len(cached_rows), len(cached_ids), FEED_CACHE_TTL_SECONDS,
            )
            return [_to_response(row) for row in cached_rows]
        logger.info(
            "feed:path=cache_miss_empty_page user=%s page=%s ids=%s",
            user_id, page, len(page_ids),
        )

    signals = sig.collect_user_signals(db, user_id)
    # Soft signals from recent impressions so scroll-only users still
    # personalize by category instead of falling onto pure latest.
    try:
        impressed = list(
            imp.recent_impressions_for_viewer(
                buyer_id=user_id,
                session_id=None,
                hours=72,
                limit=40,
            )
        )
        if impressed:
            try:
                cat_resp = (
                    db.table("products")
                    .select("category")
                    .in_("id", impressed[:40])
                    .execute()
                )
                for p in cat_resp.data or []:
                    if p.get("category"):
                        signals["categories"].add(p["category"])
            except Exception:
                pass
    except Exception as exc:
        logger.info("feed: impression soft-signals skipped: %s", exc)

    has_personal_signals = bool(
        signals["categories"]
        or signals["followed_shop_ids"]
        or signals["search_terms"]
        or signals["interactions"]
    )

    interaction_ids = _interaction_product_ids(signals)
    embedding_map = _load_embeddings_for_ids(db, interaction_ids)
    user_vector = sig.build_user_preference_vector(signals, embedding_map)

    taste_scores: dict[str, float] = {}
    vector_rows: list[dict[str, Any]] = []
    if user_vector:
        vector_rows, taste_scores = _match_products_by_vector(
            db,
            user_vector,
            match_count=VECTOR_MATCH_COUNT,
            exclude_ids=None,
        )

    candidates = _fetch_candidates(db, signals)
    if vector_rows:
        candidates = _hydrate_candidate_details(db, vector_rows + candidates)

    if not candidates:
        logger.warning("feed:path=fallback_latest user=%s reason=no_candidates", user_id)
        return get_latest_feed(db, limit)

    if user_vector and not taste_scores:
        sample_ids = [str(c["id"]) for c in candidates[:120] if c.get("id")]
        sample_emb = _load_embeddings_for_ids(db, sample_ids)
        for pid, emb in sample_emb.items():
            taste_scores[pid] = max(0.0, cosine_similarity(user_vector, emb))

    product_ids = [str(p["id"]) for p in candidates if p.get("id")]
    shop_ids = list({str(p["shop_id"]) for p in candidates if p.get("shop_id")})

    shop_meta = sig.collect_shop_meta(db, shop_ids)
    velocity_map = sig.collect_velocity_map(db, product_ids)
    boost_map = sig.collect_boost_map(db, product_ids)
    shop_id_by_product = {
        str(p["id"]): str(p.get("shop_id", "")) for p in candidates if p.get("id")
    }
    fraud_map = sig.collect_fraud_severity(db, product_ids, shop_id_by_product, shop_meta)

    shop_impressions = imp.shop_impressions_for_listings(
        shop_id_by_product, hours=C.EXPOSURE_WINDOW_HOURS
    )
    exposure_multiplier = S.build_exposure_multiplier(shop_impressions)

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
        taste_scores=taste_scores,
        now=now,
    )
    placement_cap = min(FEED_CACHE_MAX_IDS, max(limit * 8, 240))
    placed = rank_and_place(
        candidates,
        score_fn,
        boost_map=boost_map,
        shop_meta=shop_meta,
        signals=signals,
        limit=placement_cap,
    )

    ranked_ids = [
        str(item["product"].get("id"))
        for item in placed
        if item.get("product") and item["product"].get("id")
    ]
    _save_cached_ranked_ids(
        db,
        user_id,
        ranked_ids,
        preference_vector=user_vector,
        candidate_count=len(candidates),
    )

    logger.info(
        "feed:path=scored_cached user=%s pool=%d ranked=%d taste=%d vector=%s signals=%s ann=%d ttl_s=%s",
        user_id,
        len(candidates),
        len(ranked_ids),
        len(taste_scores),
        "yes" if user_vector else "no",
        "yes" if has_personal_signals else "organic",
        len(vector_rows),
        FEED_CACHE_TTL_SECONDS,
    )

    page_ids = _page_ids_from_ranked(
        ranked_ids,
        page=page,
        limit=limit,
        exclude_ids=exclude_ids,
        user_id=user_id,
        session_id=session_id,
    )
    page_rows = _fetch_products_by_ids(db, page_ids)
    if page_rows:
        return [_to_response(row) for row in page_rows]

    by_id = {
        str(item["product"].get("id")): item["product"]
        for item in placed
        if item.get("product") and item["product"].get("id")
    }
    return [_to_response(by_id[pid]) for pid in page_ids if pid in by_id]
