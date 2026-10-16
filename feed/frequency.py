"""Per-viewer daily appearance caps from the seller's plan.

`frequency_per_day` caps how many times one viewer sees a listing per day;
`frequency_per_day_boosted` replaces it while the listing has an active boost.
0 means "no cap". The day starts at midnight Africa/Kampala. Counts come from
`listing_impressions` (one row per viewer/listing per 10 minutes).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Africa/Kampala")
_TIER_TTL_S = 300.0
_tier_cache: dict[str, tuple[float, str]] = {}


def day_start_utc(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def cap_for(plan: dict[str, Any], boosted: bool) -> int:
    boosted_cap = int(plan.get("frequency_per_day_boosted") or 0)
    if boosted and boosted_cap > 0:
        return boosted_cap
    return int(plan.get("frequency_per_day") or 0)


def viewer_counts_today(client: Any, *, buyer_id: str | None, session_id: str | None) -> dict[str, int]:
    if not buyer_id and not session_id:
        return {}
    q = (
        client.table("listing_impressions")
        .select("listing_id")
        .gte("created_at", day_start_utc().isoformat())
        .limit(5000)
    )
    q = q.eq("buyer_id", buyer_id) if buyer_id else q.eq("session_id", session_id)
    counts: dict[str, int] = {}
    for row in q.execute().data or []:
        lid = str(row.get("listing_id") or "")
        if lid:
            counts[lid] = counts.get(lid, 0) + 1
    return counts


def _shop_tiers(client: Any, shop_ids: list[str]) -> dict[str, str]:
    from payments.plan_service import effective_plan_tier

    now = time.monotonic()
    out: dict[str, str] = {}
    for sid in shop_ids:
        hit = _tier_cache.get(sid)
        if hit and now - hit[0] < _TIER_TTL_S:
            out[sid] = hit[1]
    missing = [s for s in shop_ids if s not in out]
    if missing:
        shops = client.table("shops").select("id,owner_id").in_("id", missing).execute().data or []
        owners = list({str(s["owner_id"]) for s in shops if s.get("owner_id")})
        users = (
            client.table("users").select("id,plan_tier,plan_expires_at").in_("id", owners).execute().data or []
            if owners else []
        )
        tier_by_owner = {str(u["id"]): effective_plan_tier(u.get("plan_tier"), u.get("plan_expires_at")) for u in users}
        for s in shops:
            tier = tier_by_owner.get(str(s.get("owner_id")), effective_plan_tier(None))
            out[str(s["id"])] = tier
            _tier_cache[str(s["id"])] = (now, tier)
    return out


def capped_listing_ids(
    client: Any,
    *,
    buyer_id: str | None,
    session_id: str | None,
    boosted_ids: set[str] | None = None,
) -> set[str]:
    """Listings this viewer already saw as often as the seller's plan allows today."""
    from payments.plans import get_plan

    try:
        counts = viewer_counts_today(client, buyer_id=buyer_id, session_id=session_id)
        if not counts:
            return set()
        ids = list(counts)
        products = []
        for i in range(0, len(ids), 200):
            products += client.table("products").select("id,shop_id").in_("id", ids[i : i + 200]).execute().data or []
        shop_by_listing = {str(p["id"]): str(p.get("shop_id") or "") for p in products}
        shop_ids = list({s for s in shop_by_listing.values() if s})
        tiers = _shop_tiers(client, shop_ids)
        if buyer_id:
            # Sellers always see their own ads (keeps "Find in feed" scroll-to working).
            own = client.table("shops").select("id").eq("owner_id", buyer_id).execute().data or []
            own_ids = {str(r["id"]) for r in own}
            shop_by_listing = {k: v for k, v in shop_by_listing.items() if v not in own_ids}
        boosted_ids = boosted_ids or set()
        capped = set()
        for lid, n in counts.items():
            shop = shop_by_listing.get(lid)
            if not shop:
                continue
            cap = cap_for(get_plan(tiers.get(shop)), lid in boosted_ids)
            if cap > 0 and n >= cap:
                capped.add(lid)
        return capped
    except Exception as exc:  # noqa: BLE001
        logger.warning("frequency caps skipped: %s", exc)
        return set()
