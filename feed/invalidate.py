from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def rescore(listing_ids: Iterable[str], limit: int = 50) -> None:
    from ranking.service import calculate_listing_score

    for lid in list(listing_ids)[:limit]:
        try:
            calculate_listing_score(str(lid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rescore %s failed: %s", lid, exc)


def invalidate_feed_caches(client: Any | None = None, *, notify_next: bool = True) -> None:
    from feed import boost_slots, composite, snapshots

    if client is None:
        from db.supabase import get_supabase_admin

        client = get_supabase_admin()
    snapshots.invalidate(client)
    composite._guest_home_cache.clear()
    boost_slots.clear_cache()
    if notify_next:
        def _bust() -> None:
            try:
                from core.next_cache import revalidate_nextjs_cache_tag

                revalidate_nextjs_cache_tag("products")
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_bust, daemon=True).start()


def after_rank_change(listing_ids: Iterable[str], client: Any | None = None) -> None:
    rescore(listing_ids)
    invalidate_feed_caches(client)
