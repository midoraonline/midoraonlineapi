from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from feed import config as C
from feed.signals import parse_timestamp

logger = logging.getLogger(__name__)


def engagement(stats: dict[str, int]) -> float:
    return float(
        stats.get("unique_viewers", 0)
        + 2 * stats.get("likes", 0)
        + 3 * (stats.get("whatsapp_clicks", 0) + stats.get("call_clicks", 0))
        + 4 * stats.get("messages", 0)
        - 5 * stats.get("reports", 0)
    )


def decayed(raw: float, age_hours: float) -> float:
    return raw / (1.0 + max(0.0, age_hours) / 24.0) ** 1.5


def qualifies(stats: dict[str, int]) -> bool:
    actions = (
        stats.get("likes", 0)
        + stats.get("whatsapp_clicks", 0)
        + stats.get("call_clicks", 0)
        + stats.get("messages", 0)
    )
    return stats.get("unique_viewers", 0) >= C.TRENDING_MIN_UNIQUE_VIEWERS and actions >= C.TRENDING_MIN_ACTIONS


def pick(rows: list[dict[str, Any]], stats: dict[str, dict[str, int]], now: datetime) -> set[str]:
    """Top share per type lane by decayed organic engagement. rows: id, lane, posted_at."""
    by_lane: dict[str, list[tuple[float, str]]] = {}
    lane_size: dict[str, int] = {}
    for row in rows:
        lane = row["lane"]
        lane_size[lane] = lane_size.get(lane, 0) + 1
        s = stats.get(row["id"])
        if not s or not qualifies(s):
            continue
        posted = parse_timestamp(row.get("posted_at"))
        age = (now - posted).total_seconds() / 3600.0 if posted else C.TRENDING_WINDOW_HOURS
        score = decayed(engagement(s), age)
        if score > 0:
            by_lane.setdefault(lane, []).append((score, row["id"]))
    out: set[str] = set()
    for lane, scored in by_lane.items():
        keep = max(1, math.ceil(C.TRENDING_TOP_SHARE * lane_size[lane]))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out.update(pid for _, pid in scored[:keep])
    return out


def trending_ids(client: Any, rows: list[dict[str, Any]], now: datetime) -> set[str]:
    if not rows:
        return set()
    from shop.listing_stats import by_listing

    try:
        stats = by_listing(client, [r["id"] for r in rows], now - timedelta(hours=C.TRENDING_WINDOW_HOURS))
    except Exception as exc:  # noqa: BLE001
        logger.info("trending stats skipped: %s", exc)
        return set()
    return pick(rows, stats, now)
