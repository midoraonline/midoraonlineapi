"""Feed service with per-user personalization.

Algorithm feed scores each product using:
  - Vector similarity  — cosine match vs. time-decayed user preference vector
  - Interaction affinity — weighted views/likes with exponential time decay
  - Shop follow boost    — products from followed shops
  - Search match         — recent queries in title/description (fallback)
  - Category match       — preferred categories (fallback when no vectors)
  - Freshness            — created within last 7 days
  - Engagement           — listing_score from DB (capped)
  - Boosted              — active boost (Tier 1, pinned to top of feed)

Ranking architecture — Tiered Personalization (Pinned Ranking):
  Both tiers are scored by the same formula, but Tier 1 (boosted) is
  array-concatenated before Tier 2 (organic), guaranteeing that every
  boosted item appears before every organic item while both are still
  ordered by the user's personal preference vector.

  Final Feed Pool = [Sorted Tier 1 (Boosted)] + [Sorted Tier 2 (Organic)]

Every page load re-scores the entire pool from live user signals so
interactions (likes, views, follows) are reflected immediately.
"""

from __future__ import annotations

import logging
import math
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

# Implicit-feedback weights
W_LIKE = 5.0
W_VIEW = 1.0
W_SAVE = 4.0
W_WHATSAPP = 3.0
W_MESSAGE = 4.0
DECAY_LAMBDA_PER_DAY = 0.01

# Score bonuses
VECTOR_SCORE_SCALE = 80.0
CATEGORY_MATCH_BOOST = 30.0
SEARCH_MATCH_BOOST = 20.0
SEARCH_MATCH_MAX = 2       # cap search-term matches to prevent score explosion
FRESHNESS_BOOST = 5.0
FOLLOWED_SHOP_BOOST = 50.0
SEEN_DEMOTION = 50.0       # aggressively de-prioritise seen items
LISTING_SCORE_CAP = 60.0   # prevent global engagement from overwhelming personal signals
DIVERSITY_PENALTY_THRESHOLD = 4   # more than N products from same shop triggers penalty
DIVERSITY_PENALTY_PER_EXTRA = 3.0

_PRODUCT_SELECT = (
    "id,shop_id,title,description,category,item_type,price_ugx,stock_quantity,"
    "image_urls,is_published,status,listing_score,location_name,created_at,"
    "view_count,embedding,embedding_source_hash"
)


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
        "saved_product_ids": set(),
        "followed_shop_ids": set(),
        "interactions": [],
    }

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
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "like",
                    "weight": W_LIKE * _time_decay(item.get("created_at"), now),
                }
            )
        _add_categories(liked_ids)
    except Exception as exc:
        logger.warning("Failed to collect likes: %s", exc)

    # --- Views ---
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
        _add_categories(viewed_ids)
    except Exception as exc:
        logger.warning("Failed to collect views: %s", exc)

    # --- Saves ---
    try:
        saved_resp = (
            client.table("listing_events")
            .select("listing_id,created_at")
            .eq("buyer_id", user_id)
            .eq("event_type", "saved")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        saved_ids: list[str] = []
        for item in saved_resp.data or []:
            pid = str(item["listing_id"])
            saved_ids.append(pid)
            signals["saved_product_ids"].add(pid)
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "save",
                    "weight": W_SAVE * _time_decay(item.get("created_at"), now),
                }
            )
        _add_categories(saved_ids)
    except Exception as exc:
        logger.warning("Failed to collect saves: %s", exc)

    # --- WhatsApp clicks ---
    try:
        wa_resp = (
            client.table("listing_events")
            .select("listing_id,created_at")
            .eq("buyer_id", user_id)
            .eq("event_type", "whatsapp_clicked")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        for item in wa_resp.data or []:
            pid = str(item["listing_id"])
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "whatsapp",
                    "weight": W_WHATSAPP * _time_decay(item.get("created_at"), now),
                }
            )
    except Exception as exc:
        logger.warning("Failed to collect whatsapp clicks: %s", exc)

    # --- Messages ---
    try:
        msg_resp = (
            client.table("listing_events")
            .select("listing_id,created_at")
            .eq("buyer_id", user_id)
            .eq("event_type", "messaged")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        for item in msg_resp.data or []:
            pid = str(item["listing_id"])
            signals["interactions"].append(
                {
                    "product_id": pid,
                    "type": "message",
                    "weight": W_MESSAGE * _time_decay(item.get("created_at"), now),
                }
            )
    except Exception as exc:
        logger.warning("Failed to collect messages: %s", exc)

    # --- Followed shops ---
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
    shop_candidate_counts: dict[str, int] | None = None,
) -> float:
    """Compute the personalization score for a single product.

    Formula:
        S = (cosine_similarity × VECTOR_SCORE_SCALE)
            + category_match_bonus
            + search_match_bonus (capped at SEARCH_MATCH_MAX matches)
            + followed_shop_bonus
            + freshness_bonus
            + min(listing_score, LISTING_SCORE_CAP)
            - seen_demotion
            - shop_diversity_penalty  (if same shop has many candidates)

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

    # --- Search-term match bonus (capped) ---
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    search_matches = 0
    for term in signals["search_terms"]:
        if term in title or term in desc:
            search_matches += 1
    score += min(search_matches, SEARCH_MATCH_MAX) * SEARCH_MATCH_BOOST

    # --- Followed shop boost ---
    if shop_id and shop_id in signals["followed_shop_ids"]:
        score += FOLLOWED_SHOP_BOOST

    # --- Freshness bonus ---
    created_str = product.get("created_at")
    if created_str:
        created = _parse_timestamp(created_str)
        if created and created >= cutoff:
            score += FRESHNESS_BOOST

    # --- Engagement signal (capped so global popularity doesn't overwhelm taste) ---
    score += min(float(product.get("listing_score") or 0), LISTING_SCORE_CAP)

    # --- Already-seen demotion ---
    if (
        pid in signals["liked_product_ids"]
        or pid in signals["viewed_product_ids"]
        or pid in signals.get("saved_product_ids", set())
    ):
        score -= SEEN_DEMOTION

    # --- Shop diversity penalty ---
    if shop_id and shop_candidate_counts:
        shop_count = shop_candidate_counts.get(shop_id, 0)
        if shop_count > DIVERSITY_PENALTY_THRESHOLD:
            score -= (shop_count - DIVERSITY_PENALTY_THRESHOLD) * DIVERSITY_PENALTY_PER_EXTRA

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
      Every page recomputes the full ranking from live user signals so
      interactions (likes, views, follows) are immediately reflected.

    Unauthenticated:
      Falls back to a recency-sorted feed (no personalization).
    """
    if not user_id:
        return get_latest_feed(client, limit)

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit

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
    # STAGE 1b: Shop candidate density — used for diversity penalty
    # -----------------------------------------------------------------------
    shop_candidate_counts: dict[str, int] = {}
    for p in candidates:
        sid = str(p.get("shop_id", ""))
        if sid:
            shop_candidate_counts[sid] = shop_candidate_counts.get(sid, 0) + 1

    # -----------------------------------------------------------------------
    # STAGE 1c: Resolve active boost registrations for the candidate pool
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
        score = _product_score(
            product, signals, cutoff, user_vector,
            shop_candidate_counts=shop_candidate_counts,
        )

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

    # -----------------------------------------------------------------------
    # STAGE 4: Hydrate and return the requested page slice
    # -----------------------------------------------------------------------
    page_items = [item["product"] for item in combined_feed[start_idx:end_idx]]
    return [ProductResponse(**p) for p in page_items]
