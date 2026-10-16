"""One place to drop every cached feed order after a ranking change (boost, repost, end boost).

Layers: in-process guest snapshot + `feed_rank_snapshots` row, the composite guest
home cache, the active-boost list used for boosted slots, and the Next.js tag/path
cache (fire-and-forget).

Signed-in `user_feed_cache` rows are NOT touched: boosts reach those feeds at read
time through boosted slots (feed/boost_slots.py) and reposts through the fresh-item
merge, so one boost no longer forces every active user to re-score at once.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)

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
        from feed import boost_slots

        boost_slots.clear_cache()
    except Exception:  # noqa: BLE001
        pass
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
