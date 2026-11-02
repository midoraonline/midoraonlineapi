from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from feed import config as C
from feed.signals import parse_timestamp
from feed.stages import lane_of


def _hours_to_close(meta: Any, now: datetime) -> float | None:
    raw = str((meta or {}).get("deadline") or "").strip() if isinstance(meta, dict) else ""
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (day + timedelta(days=1) - now).total_seconds() / 3600.0


def visible_status(
    *,
    placement: str,
    item_type: str | None,
    listing_meta: Any,
    posted_at: Any,
    now: datetime,
    trending: bool = False,
) -> str | None:
    if placement in ("sponsored", "boosted"):
        return placement
    if lane_of(item_type) == "opportunity":
        hours = _hours_to_close(listing_meta, now)
        if hours is not None and 0 < hours <= C.CLOSING_SOON_HOURS:
            return "closing_soon"
    if trending:
        return "trending"
    posted = parse_timestamp(posted_at)
    if posted and (now - posted).total_seconds() <= C.NEW_BADGE_HOURS * 3600:
        return "new"
    return None
