"""Near-duplicate detection (Phase 1 Should).

Two cheap, proven signals — no reverse-image search:
1. Same-seller title + price re-post against their other active/pending listings
2. Perceptual aHash overlap with hashes from other active listings

Hits send the listing to needs_review (not hard reject) so admins can act.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import config
from .phash import _hamming

logger = logging.getLogger(__name__)

_HASHES_TABLE = "listing_image_hashes"
# How many stored hashes to scan per listing (bounded for drain budget).
_HASH_SCAN_LIMIT = 4000


def check_title_price_repost(
    client: Any,
    *,
    seller_id: str | None,
    product_id: str | None,
    title: str,
    price_ugx: float | None,
) -> Optional[str]:
    """Return a reason when the same seller re-posts the same title+price."""
    if not seller_id:
        return None
    title_norm = " ".join((title or "").strip().lower().split())
    if len(title_norm) < 4:
        return None
    try:
        price = float(price_ugx or 0)
    except (TypeError, ValueError):
        price = 0.0

    try:
        shops = (
            client.table("shops")
            .select("id")
            .eq("owner_id", seller_id)
            .execute()
        )
        shop_ids = [str(s["id"]) for s in (shops.data or []) if s.get("id")]
        if not shop_ids:
            return None

        q = (
            client.table("products")
            .select("id, title, price_ugx, status")
            .in_("shop_id", shop_ids)
            .in_("status", ["active", "pending_review", "needs_review"])
            .limit(80)
        )
        rows = q.execute().data or []
    except Exception as exc:
        logger.debug("near_dupe title/price query failed: %s", exc)
        return None

    for row in rows:
        rid = str(row.get("id") or "")
        if product_id and rid == str(product_id):
            continue
        other_title = " ".join((row.get("title") or "").strip().lower().split())
        try:
            other_price = float(row.get("price_ugx") or 0)
        except (TypeError, ValueError):
            other_price = -1.0
        if other_title == title_norm and abs(other_price - price) < 0.5:
            return f"same-seller title/price re-post matches listing {rid}"
    return None


def check_phash_against_catalog(
    client: Any,
    *,
    product_id: str | None,
    image_hashes: list[Optional[int]],
) -> Optional[str]:
    """Return a reason when an image aHash is near another active listing's."""
    candidates = [h for h in image_hashes if h is not None]
    if not candidates:
        return None

    try:
        r = (
            client.table(_HASHES_TABLE)
            .select("product_id, phash")
            .order("created_at", desc=True)
            .limit(_HASH_SCAN_LIMIT)
            .execute()
        )
        rows = r.data or []
    except Exception as exc:
        # Table may not exist until migration 040 is applied — soft-skip.
        logger.debug("near_dupe hash catalog unavailable: %s", exc)
        return None

    threshold = config.phash_distance_threshold
    for row in rows:
        other_pid = str(row.get("product_id") or "")
        if product_id and other_pid == str(product_id):
            continue
        try:
            other_h = int(row["phash"])
        except (TypeError, ValueError, KeyError):
            continue
        for h in candidates:
            if _hamming(h, other_h) <= threshold:
                return f"image near-duplicate of product {other_pid}"
    return None


def persist_listing_hashes(
    client: Any,
    *,
    product_id: str | None,
    image_hashes: list[Optional[int]],
) -> None:
    """Upsert aHashes for an approved/active listing so future posts can match."""
    if not product_id:
        return
    rows = []
    seen: set[int] = set()
    for h in image_hashes:
        if h is None or h in seen:
            continue
        seen.add(h)
        rows.append({"product_id": product_id, "phash": int(h)})
    if not rows:
        return
    try:
        # Replace prior hashes for this product, then insert fresh set.
        client.table(_HASHES_TABLE).delete().eq("product_id", product_id).execute()
        client.table(_HASHES_TABLE).insert(rows).execute()
    except Exception as exc:
        logger.debug("persist_listing_hashes failed: %s", exc)
