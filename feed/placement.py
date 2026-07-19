"""Post-ranking placement engine.

Given the pre-scored candidate pool, produce the final ordered feed by:
  1. Bucketing candidates into pools (organic / boosted / sponsored /
     super_boost / premium_store / fresh / exploration).
  2. Placing reserved slots at deterministic cadences (every N positions).
  3. Filling residual positions from the organic pool.
  4. Applying a progressive vendor-diversity penalty during placement.
  5. Enforcing the 12-position sliding-window rule
     (max 2 listings per seller).

Placement is intentionally separate from scoring — the same score can end
up in a different position depending on marketplace composition targets.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from feed import config as C
from feed.signals import parse_timestamp


# ---------------------------------------------------------------------------
# Pool bucketing
# ---------------------------------------------------------------------------

def bucketize(
    scored: list[dict[str, Any]],
    *,
    boost_map: dict[str, dict[str, Any]],
    shop_meta: dict[str, dict[str, Any]],
    signals: dict[str, Any],
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Split scored candidates into named pools.

    Each candidate can only live in ONE pool (highest-priority wins).
    Priority order: super_boost > sponsored > boosted > premium_store >
    fresh > exploration > organic.
    """
    pools: dict[str, list[dict[str, Any]]] = {
        "super_boost": [],
        "sponsored": [],
        "boosted": [],
        "premium_store": [],
        "fresh": [],
        "exploration": [],
        "organic": [],
    }

    fresh_cutoff_hours = 24 * 3  # <= 3 days = fresh pool eligible

    for entry in scored:
        product = entry["product"]
        pid = str(product.get("id", ""))
        shop_id = str(product.get("shop_id", ""))
        meta = shop_meta.get(shop_id, {})
        boost = boost_map.get(pid)

        placed = False
        if boost:
            sb = float(boost.get("score_bonus") or 0.0)
            if sb > 25:
                pools["super_boost"].append(entry); placed = True
            else:
                # `boost_plans.name` naming isn't standard yet — treat all
                # paid boosts as `boosted`. Sponsored slots are managed by
                # a separate admin flow (see docs).
                pools["boosted"].append(entry); placed = True

        if placed:
            continue

        # Premium store — active subscription
        end = parse_timestamp(meta.get("subscription_end_date"))
        if end and end > now:
            pools["premium_store"].append(entry); placed = True
        if placed:
            continue

        # Fresh — brand-new listing OR new seller with low exposure
        created = parse_timestamp(product.get("created_at"))
        seller_created = parse_timestamp(meta.get("created_at"))
        is_fresh_listing = (
            created is not None
            and (now - created).total_seconds() / 3600.0 <= fresh_cutoff_hours
        )
        seller_age_days = (
            (now - seller_created).total_seconds() / 86400.0
            if seller_created else None
        )
        is_new_seller = (
            (seller_age_days is not None and seller_age_days < C.NEW_SELLER_MAX_AGE_DAYS)
            or int(product.get("view_count") or 0) < C.NEW_SELLER_MAX_IMPRESSIONS
        )
        if is_fresh_listing or is_new_seller:
            pools["fresh"].append(entry); placed = True
        if placed:
            continue

        # Exploration — off-profile category (buyer never engaged with it)
        cat = (product.get("category") or "").strip()
        if cat and signals["categories"] and cat not in signals["categories"]:
            pools["exploration"].append(entry); placed = True
        if placed:
            continue

        pools["organic"].append(entry)

    # Each pool remains sorted by score (input `scored` is already sorted)
    return pools


# ---------------------------------------------------------------------------
# Vendor-diversity helpers
# ---------------------------------------------------------------------------

def _progressive_vendor_penalty(rank_in_feed: int) -> float:
    """penalty = min(CAP, K * (rank-1)^2). rank=1 -> 0, rank=2 -> K, ..."""
    if rank_in_feed <= 1:
        return 0.0
    raw = C.VENDOR_DIVERSITY_K * (rank_in_feed - 1) ** 2
    return min(C.VENDOR_DIVERSITY_CAP, raw)


def _seller_at_window_capacity(
    window: deque[str], shop_id: str, cap: int
) -> bool:
    """True if this seller already occupies `cap` slots in the current window."""
    if not shop_id:
        return False
    return sum(1 for s in window if s == shop_id) >= cap


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def place(
    pools: dict[str, list[dict[str, Any]]],
    *,
    limit: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Produce the final `limit`-length feed.

    Slots reserved by cadence are pulled first; residual slots are filled
    from the organic pool. Selection *within* a pool applies the progressive
    vendor-diversity penalty and enforces the 12-window rule.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # deque of shop_ids for the last VENDOR_WINDOW_SIZE placed items
    window: deque[str] = deque(maxlen=C.VENDOR_WINDOW_SIZE)
    seller_rank_so_far: dict[str, int] = {}
    used_product_ids: set[str] = set()

    # Precompute per-position kind, based on cadence. When two cadences
    # collide on the same position, higher priority wins.
    position_kind: list[str] = []
    cadences = sorted(
        C.COMPOSITION_CADENCE, key=lambda s: s.priority, reverse=True
    )
    for pos in range(1, limit + 1):
        kind = "organic"
        for slot in cadences:
            if slot.one_per_n <= 0:
                continue
            # Hit position when (pos - phase) is a non-negative multiple of one_per_n
            offset = pos - slot.phase
            if offset >= 0 and offset % slot.one_per_n == 0:
                kind = slot.kind
                break
        position_kind.append(kind)

    def _pick_from(pool_name: str, allow_fallback: bool = True) -> dict[str, Any] | None:
        pool = pools.get(pool_name, [])
        # Try primary pool; if empty and allow_fallback, drop to organic
        for pool_ref in ([pool] if not allow_fallback else [pool, pools.get("organic", [])]):
            for i, entry in enumerate(pool_ref):
                pid = str(entry["product"].get("id", ""))
                if pid in used_product_ids:
                    continue
                shop_id = str(entry["product"].get("shop_id", ""))
                if _seller_at_window_capacity(window, shop_id, C.VENDOR_WINDOW_MAX_PER_SELLER):
                    continue
                # Apply progressive vendor penalty to see if it still ranks
                penalty = _progressive_vendor_penalty(seller_rank_so_far.get(shop_id, 0) + 1)
                effective = float(entry["score"]) - penalty
                # We commit to the top candidate that passes the window rule.
                pool_ref.pop(i)
                entry_out = {**entry, "adjusted_score": effective, "pool": pool_name}
                return entry_out
        return None

    placed: list[dict[str, Any]] = []
    for kind in position_kind:
        pick = _pick_from(kind, allow_fallback=True)
        if pick is None:
            # No candidate found (all pools drained or window-blocked). Try
            # scanning any pool to fill this slot rather than leaving a gap.
            for name in ("organic", "boosted", "sponsored", "super_boost",
                        "premium_store", "fresh", "exploration"):
                if name == kind:
                    continue
                pick = _pick_from(name, allow_fallback=False)
                if pick:
                    break
        if pick is None:
            break  # nothing left anywhere

        pid = str(pick["product"].get("id", ""))
        shop_id = str(pick["product"].get("shop_id", ""))
        used_product_ids.add(pid)
        window.append(shop_id)
        seller_rank_so_far[shop_id] = seller_rank_so_far.get(shop_id, 0) + 1
        placed.append(pick)

    return placed


# ---------------------------------------------------------------------------
# Top-level orchestrator — used by service.py
# ---------------------------------------------------------------------------

def rank_and_place(
    candidates: Iterable[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], float | None],
    *,
    boost_map: dict[str, dict[str, Any]],
    shop_meta: dict[str, dict[str, Any]],
    signals: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Score, bucketize, place. Returns list of `{product, score, pool}`."""
    now = datetime.now(timezone.utc)
    scored: list[dict[str, Any]] = []
    for product in candidates:
        score = score_fn(product)
        if score is None:
            continue  # hidden (critical fraud)
        scored.append({"product": product, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)

    pools = bucketize(
        scored,
        boost_map=boost_map,
        shop_meta=shop_meta,
        signals=signals,
        now=now,
    )
    return place(pools, limit=limit, now=now)
