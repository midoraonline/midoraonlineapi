"""Feed service with per-user personalization.

Algorithm feed scores each product using:
  - Vector similarity  — cosine match vs. time-decayed user preference vector
  - Interaction affinity — weighted views/likes with exponential time decay
  - Shop follow boost    — products from followed shops
  - Search match         — recent queries in title/description (fallback)
  - Category match       — preferred categories (fallback when no vectors)
  - Freshness            — created within last 7 days
  - Engagement           — listing_score from DB
  - Boosted              — active boost (Tier 1, pinned to top of feed)

Ranking architecture — Tiered Personalization (Pinned Ranking):
  Both tiers are scored by the same formula, but Tier 1 (boosted) is
  array-concatenated before Tier 2 (organic), guaranteeing that every
  boosted item appears before every organic item while both are still
  ordered by the user's personal preference vector.

  Final Feed Pool = [Sorted Tier 1 (Boosted)] + [Sorted Tier 2 (Organic)]

In-memory pagination cache:
  The full ranked ID list is cached per user (TTL = CACHE_TTL_SECONDS).
  Subsequent page requests are O(1) slice lookups — no re-scoring.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from db.supabase import Client
from feed.embeddings import cosine_similarity, parse_embedding, weighted_average
from shop.schemas import ProductResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FRESHNESS_DAYS = 7
CANDIDATE_POOL_MAX = 500
CACHE_TTL_SECONDS = 300          # 5 minutes per-user feed cache

# Implicit-feedback weights (Phase 1 heuristic)
W_LIKE = 5.0
W_VIEW = 1.0
DECAY_LAMBDA_PER_DAY = 0.01

# Score bonuses
VECTOR_SCORE_SCALE = 40.0
CATEGORY_MATCH_BOOST = 30.0
SEARCH_MATCH_BOOST = 20.0
FRESHNESS_BOOST = 10.0
FOLLOWED_SHOP_BOOST = 25.0
SEEN_DEMOTION = 5.0

_PRODUCT_SELECT = (
    "id,shop_id,title,description,category,item_type,price_ugx,stock_quantity,"
    "image_urls,is_published,status,listing_score,location_name,created_at,"
    "view_count,embedding,embedding_source_hash"
)


# ---------------------------------------------------------------------------
# Lightweight in-process TTL cache (no extra dependencies)
# ---------------------------------------------------------------------------

class _TTLCache:
    """Thread-safe dict-based TTL store for per-user feed ID lists."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS) -> None:
        self._store: dict[str, tuple[list[str], float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(self, key: str) -> list[str] | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ids, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return ids

    def set(self, key: str, ids: list[str]) -> None:
        with self._lock:
            self._store[key] = (ids, time.monotonic() + self._ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


# Module-level singleton — shared across all requests within the same worker
feed_cache = _TTLCache()


# ---------------------------------------------------------------------------
# Helpers — query utilities
# ---------------------------------------------------------------------------

def _active_products_query(client: Client) -> Any:
    return (
        client.table("products")
        .select(_PRODUCT_SELECT)
        .eq("status", "active")
        .eq("is_published", True)
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


def _time_decay(created_at: Any, now: datetime) -> float:
    ts = _parse_timestamp(created_at)
    if ts is None:
        return 1.0
    days = max((now - ts).total_seconds() / 86400.0, 0.0)
    return math.exp(-DECAY_LAMBDA_PER_DAY * days)


# ---------------------------------------------------------------------------
# Public fallback feed
# ---------------------------------------------------------------------------

def get_latest_feed(client: Client, limit: int = 20) -> list[ProductResponse]:
    """Fetch the latest active products sorted by creation date."""
    try:
        resp = (
            _active_products_query(client)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [ProductResponse(**item) for item in resp.data]
    except Exception as exc:
        logger.warning("get_latest_feed failed, falling back: %s", exc)
        resp = (
            client.table("products")
            .select("*")
            .eq("is_published", True)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [ProductResponse(**item) for item in resp.data]


# ---------------------------------------------------------------------------
# Signal collection
# ---------------------------------------------------------------------------

def _collect_user_signals(client: Client, user_id: str | None) -> dict[str, Any]:
    """Gather user preference signals with timestamps for decay weighting."""
    signals: dict[str, Any] = {
        "categories": set(),
        "search_terms": [],
        "liked_product_ids": set(),
        "viewed_product_ids": set(),
        "followed_shop_ids": set(),
        "interactions": [],
    }

    if not user_id:
        return signals

    now = datetime.now(timezone.utc)

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
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "like",
                    "weight": W_LIKE * _time_decay(item.get("created_at"), now),
                }
            )
        if liked_ids:
            cat_resp = (
                client.table("products")
                .select("category")
                .in_("id", liked_ids)
                .execute()
            )
            for p in cat_resp.data or []:
                if p.get("category"):
                    signals["categories"].add(p["category"])
    except Exception as exc:
        logger.warning("Failed to collect likes: %s", exc)

    try:
        viewed_resp = (
            client.table("listing_events")
            .select("listing_id,created_at")
            .eq("buyer_id", user_id)
            .eq("event_type", "viewed")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        viewed_ids: list[str] = []
        for item in viewed_resp.data or []:
            pid = str(item["listing_id"])
            viewed_ids.append(pid)
            signals["viewed_product_ids"].add(pid)
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "view",
                    "weight": W_VIEW * _time_decay(item.get("created_at"), now),
                }
            )
        if viewed_ids:
            cat_resp = (
                client.table("products")
                .select("category")
                .in_("id", viewed_ids)
                .execute()
            )
            for p in cat_resp.data or []:
                if p.get("category"):
                    signals["categories"].add(p["category"])
    except Exception as exc:
        logger.warning("Failed to collect views: %s", exc)

    try:
        follow_resp = (
            client.table("shop_follows")
            .select("shop_id,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        for row in follow_resp.data or []:
            sid = str(row["shop_id"])
            signals["followed_shop_ids"].add(sid)
    except Exception as exc:
        logger.warning("Failed to collect shop follows: %s", exc)

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
        logger.warning("Failed to collect search history: %s", exc)

    return signals


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _fetch_product_embeddings(
    client: Client,
    product_ids: list[str],
) -> dict[str, list[float]]:
    if not product_ids:
        return {}
    try:
        resp = (
            client.table("products")
            .select("id,embedding")
            .in_("id", product_ids)
            .execute()
        )
    except Exception as exc:
        logger.warning("Failed to fetch product embeddings: %s", exc)
        return {}

    out: dict[str, list[float]] = {}
    for row in resp.data or []:
        embedding = parse_embedding(row.get("embedding"))
        if embedding:
            out[str(row["id"])] = embedding
    return out


def _build_user_preference_vector(
    client: Client,
    signals: dict[str, Any],
) -> list[float] | None:
    """Weighted average of embeddings from liked/viewed products."""
    interaction_ids = list(
        {i["product_id"] for i in signals["interactions"] if i.get("product_id")}
    )
    embeddings_map = _fetch_product_embeddings(client, interaction_ids)

    weighted: list[tuple[list[float], float]] = []
    for interaction in signals["interactions"]:
        embedding = embeddings_map.get(interaction["product_id"])
        if not embedding:
            continue
        weight = float(interaction.get("weight") or 0.0)
        if weight > 0:
            weighted.append((embedding, weight))

    return weighted_average(weighted)


# ---------------------------------------------------------------------------
# Candidate pool fetcher
# ---------------------------------------------------------------------------

def _fetch_candidate_products(
    client: Client,
    signals: dict[str, Any],
    pool_limit: int = CANDIDATE_POOL_MAX,
) -> list[dict[str, Any]]:
    """Build a candidate pool instead of scoring the entire catalog."""
    seen_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []

    def _add_rows(rows: list[dict[str, Any]] | None) -> None:
        for row in rows or []:
            pid = str(row.get("id", ""))
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                candidates.append(row)

    try:
        top_r = (
            _active_products_query(client)
            .order("listing_score", desc=True)
            .order("created_at", desc=True)
            .limit(min(350, pool_limit))
            .execute()
        )
        _add_rows(top_r.data)
    except Exception as exc:
        logger.warning("Candidate fetch (top scored) failed: %s", exc)

    if signals["followed_shop_ids"]:
        try:
            follow_r = (
                _active_products_query(client)
                .in_("shop_id", list(signals["followed_shop_ids"]))
                .order("listing_score", desc=True)
                .limit(200)
                .execute()
            )
            _add_rows(follow_r.data)
        except Exception as exc:
            logger.warning("Candidate fetch (followed shops) failed: %s", exc)

    if signals["categories"]:
        try:
            cat_r = (
                _active_products_query(client)
                .in_("category", list(signals["categories"]))
                .order("listing_score", desc=True)
                .limit(200)
                .execute()
            )
            _add_rows(cat_r.data)
        except Exception as exc:
            logger.warning("Candidate fetch (categories) failed: %s", exc)

    if len(candidates) < pool_limit:
        try:
            recent_r = (
                _active_products_query(client)
                .order("created_at", desc=True)
                .limit(pool_limit)
                .execute()
            )
            _add_rows(recent_r.data)
        except Exception as exc:
            logger.warning("Candidate fetch (recent) failed: %s", exc)

    return candidates[:pool_limit]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _product_score(
    product: dict[str, Any],
    signals: dict[str, Any],
    cutoff: datetime,
    user_vector: list[float] | None,
) -> float:
    """Compute the personalization score for a single product.

    Formula:
        S = (cosine_similarity × VECTOR_SCORE_SCALE)
            + category_match_bonus
            + search_match_bonus
            + followed_shop_bonus
            + freshness_bonus
            + listing_score
            - seen_demotion

    The boosted/organic split is done externally — this function is
    intentionally boost-agnostic so the same formula applies to both tiers.
    """
    score = 0.0

    cat = (product.get("category") or "").strip()
    pid = str(product.get("id", ""))
    shop_id = str(product.get("shop_id", ""))

    # --- Vector similarity (primary signal) ---
    product_vector = parse_embedding(product.get("embedding"))
    if user_vector and product_vector:
        sim = cosine_similarity(user_vector, product_vector)
        score += max(sim, 0.0) * VECTOR_SCORE_SCALE
    elif cat and cat in signals["categories"]:
        # Fallback: category affinity when no embedding is available
        score += CATEGORY_MATCH_BOOST

    # --- Search-term match bonus ---
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    for term in signals["search_terms"]:
        if term in title or term in desc:
            score += SEARCH_MATCH_BOOST

    # --- Followed shop boost ---
    if shop_id and shop_id in signals["followed_shop_ids"]:
        score += FOLLOWED_SHOP_BOOST

    # --- Freshness bonus ---
    created_str = product.get("created_at")
    if created_str:
        created = _parse_timestamp(created_str)
        if created and created >= cutoff:
            score += FRESHNESS_BOOST

    # --- Engagement signal ---
    score += float(product.get("listing_score") or 0)

    # --- Already-seen demotion ---
    if pid in signals["liked_product_ids"] or pid in signals["viewed_product_ids"]:
        score -= SEEN_DEMOTION

    return score


# ---------------------------------------------------------------------------
# Main feed entrypoint — Tiered Personalization (Pinned Ranking)
# ---------------------------------------------------------------------------

def get_algorithm_feed(
    client: Client,
    user_id: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> list[ProductResponse]:
    """Personalized feed that strictly prioritizes boosted products first.

    Architecture (Tiered Personalization / Pinned Ranking):
      Both tiers use the exact same personalization score formula so that
      the user's interest vector governs ordering within each tier.
      Tier 1 (Boosted) is then array-concatenated before Tier 2 (Organic),
      guaranteeing guaranteed placement without a variable multiplier that
      could be beaten by a high-scoring organic item.

      Final Feed Pool = [Sorted Tier 1 (Boosted)] + [Sorted Tier 2 (Organic)]

    Pagination:
      The full ranked ID list is cached in-process per user (TTL=5 min).
      Pages 2+ are O(1) slice lookups — no DB re-scoring.

    Unauthenticated:
      Falls back to a recency-sorted feed (no personalization).
    """
    if not user_id:
        return get_latest_feed(client, limit)

    cache_key = f"feed_{user_id}"
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit

    # -----------------------------------------------------------------------
    # STAGE 0: Fast O(1) cache hit — serve paginated slice from memory
    # -----------------------------------------------------------------------
    cached_ids = feed_cache.get(cache_key)
    if cached_ids:
        page_ids = cached_ids[start_idx:end_idx]
        if not page_ids:
            return []
        resp = client.table("products").select(_PRODUCT_SELECT).in_("id", page_ids).execute()
        id_to_product = {str(p["id"]): p for p in (resp.data or [])}
        ordered = [id_to_product[pid] for pid in page_ids if pid in id_to_product]
        return [ProductResponse(**p) for p in ordered]

    # -----------------------------------------------------------------------
    # STAGE 1: Data gathering — signals, vectors, candidate pool
    # -----------------------------------------------------------------------
    signals = _collect_user_signals(client, user_id)
    user_vector = _build_user_preference_vector(client, signals)
    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)

    if not user_vector and not signals["categories"] and not signals["followed_shop_ids"]:
        # No meaningful signals yet — return latest feed for new users
        return get_latest_feed(client, limit)

    candidates = _fetch_candidate_products(client, signals, pool_limit=CANDIDATE_POOL_MAX)
    if not candidates:
        return get_latest_feed(client, limit)

    # -----------------------------------------------------------------------
    # STAGE 1b: Resolve active boost registrations for the candidate pool
    # -----------------------------------------------------------------------
    boosted_ids: set[str] = set()
    product_ids = [str(p["id"]) for p in candidates if p.get("id")]
    if product_ids:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            boosts_r = (
                client.table("listing_boosts")
                .select("listing_id")
                .in_("listing_id", product_ids)
                .eq("active", True)
                .gte("ends_at", now_iso)
                .execute()
            )
            boosted_ids = {
                str(b["listing_id"]) for b in (boosts_r.data or []) if b.get("listing_id")
            }
        except Exception as exc:
            logger.warning("Boost fetch failed: %s", exc)

    # -----------------------------------------------------------------------
    # STAGE 2: Separation + independent personalized scoring
    #
    #   Every candidate is scored by _product_score() — the same formula for
    #   both tiers. The only structural difference is which list it joins.
    # -----------------------------------------------------------------------
    boosted_tier: list[dict[str, Any]] = []
    organic_tier: list[dict[str, Any]] = []

    for product in candidates:
        pid = str(product.get("id", ""))
        score = _product_score(product, signals, cutoff, user_vector)

        payload = {"product": product, "score": score}
        if pid in boosted_ids:
            boosted_tier.append(payload)
        else:
            organic_tier.append(payload)

    # -----------------------------------------------------------------------
    # STAGE 3: Independent tier sorting + deterministic concatenation
    #
    #   Each tier is sorted descending by personal score so that the most
    #   relevant boosted item leads, followed by less-relevant boosted items,
    #   then the full organic ranking begins.
    # -----------------------------------------------------------------------
    boosted_tier.sort(key=lambda x: x["score"], reverse=True)
    organic_tier.sort(key=lambda x: x["score"], reverse=True)

    # Pinned concatenation — Tier 1 takes absolute top-of-feed precedence
    combined_feed = boosted_tier + organic_tier

    # Cache the full ordered ID list for pagination continuity
    all_sorted_ids = [str(item["product"]["id"]) for item in combined_feed]
    feed_cache.set(cache_key, all_sorted_ids)

    # -----------------------------------------------------------------------
    # STAGE 4: Hydrate and return the requested page slice
    # -----------------------------------------------------------------------
    page_items = [item["product"] for item in combined_feed[start_idx:end_idx]]
    return [ProductResponse(**p) for p in page_items]
