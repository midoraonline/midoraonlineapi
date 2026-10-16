"""One place to drop every cached feed order after a ranking change (boost, repost, end boost).

Layers: in-process guest snapshot + `feed_rank_snapshots` row, the composite guest
home cache, every signed-in user's `user_feed_cache` (marked stale so the next load
re-ranks), and the Next.js tag/path cache (fire-and-forget).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)

STALE_AT = "1970-01-01T00:00:00+00:00"


def rescore(listing_ids: Iterable[str], limit: int = 50) -> None:
    try:
        from ranking.service import calculate_listing_score
    except Exception:  # noqa: BLE001
        return
    for lid in list(listing_ids)[:limit]:
        try:
            calculate_listing_score(str(lid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rescore %s failed: %s", lid, exc)


def invalidate_feed_caches(client: Any | None = None, *, notify_next: bool = True) -> None:
    if client is None:
        from db.supabase import get_supabase_admin

        client = get_supabase_admin()
    try:
        from feed import snapshots

        snapshots.invalidate(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("guest snapshot invalidate failed: %s", exc)
    try:
        from feed import composite

        composite._guest_home_cache.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        # Only rows still inside the 1h TTL need touching.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        client.table("user_feed_cache").update({"refreshed_at": STALE_AT}).gte("refreshed_at", cutoff).execute()
    except Exception as exc:  # noqa: BLE001
        logger.info("user_feed_cache stale-mark skipped: %s", exc)
    if notify_next:
        def _bust() -> None:
            try:
                from core.next_cache import revalidate_nextjs_cache_tag

                revalidate_nextjs_cache_tag("products")
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_bust, daemon=True).start()


def after_rank_change(listing_ids: Iterable[str], client: Any | None = None) -> None:
    """Rescore the listings, then drop all cached orders."""
    rescore(listing_ids)
    invalidate_feed_caches(client)
