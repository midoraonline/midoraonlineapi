from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Africa/Kampala")
CHUNK = 300
METRICS = (
    "impressions",
    "reach",
    "boosted_impressions",
    "views",
    "unique_viewers",
    "whatsapp_clicks",
    "call_clicks",
    "messages",
    "shares",
    "likes",
    "comments",
    "reviews",
    "reports",
)


def empty() -> dict[str, int]:
    return {m: 0 for m in METRICS}


def since_for(days: int, now: datetime | None = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return start.astimezone(timezone.utc)


def contacts(row: dict[str, int]) -> int:
    return row["whatsapp_clicks"] + row["call_clicks"] + row["messages"]


def with_rates(row: dict[str, int]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["contacts"] = contacts(row)
    out["view_rate"] = row["views"] / row["impressions"] if row["impressions"] else 0.0
    out["contact_rate"] = out["contacts"] / row["views"] if row["views"] else 0.0
    return out


def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[i : i + CHUNK] for i in range(0, len(ids), CHUNK)]


def by_listing(admin: Any, listing_ids: list[str], since: datetime) -> dict[str, dict[str, int]]:
    out = {lid: empty() for lid in listing_ids}
    for part in _chunks(listing_ids):
        try:
            rows = admin.rpc(
                "listing_activity_stats",
                {"p_listing_ids": part, "p_since": since.isoformat()},
            ).execute().data or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("listing_activity_stats failed (apply 063): %s", exc)
            continue
        for row in rows:
            lid = str(row.get("listing_id") or "")
            if lid in out:
                out[lid] = {m: int(row.get(m) or 0) for m in METRICS}
    return out


def totals(stats: dict[str, dict[str, int]]) -> dict[str, int]:
    out = empty()
    for row in stats.values():
        for m in METRICS:
            out[m] += row[m]
    return out


def daily(admin: Any, listing_ids: list[str], since: datetime, days: int) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for part in _chunks(listing_ids):
        try:
            rows = admin.rpc(
                "listing_activity_daily",
                {"p_listing_ids": part, "p_since": since.isoformat()},
            ).execute().data or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("listing_activity_daily failed (apply 063): %s", exc)
            continue
        for row in rows:
            day = str(row.get("day") or "")[:10]
            bucket = buckets.setdefault(day, {"impressions": 0, "views": 0, "contacts": 0})
            for key in bucket:
                bucket[key] += int(row.get(key) or 0)
    first: date = since.astimezone(TZ).date()
    series = []
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        series.append({"day": day, **buckets.get(day, {"impressions": 0, "views": 0, "contacts": 0})})
    return series
