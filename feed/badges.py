"""One card badge per listing on every surface: paid disclosure first, then status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from feed.status import visible_status

logger = logging.getLogger(__name__)

_CHUNK = 150


def _boost_rows(client: Any, ids: list[str], now_iso: str) -> list[dict[str, Any]]:
    for cols in ("listing_id,kind", "listing_id"):
        try:
            rows: list[dict[str, Any]] = []
            for i in range(0, len(ids), _CHUNK):
                rows.extend(
                    client.table("listing_boosts")
                    .select(cols)
                    .in_("listing_id", ids[i : i + _CHUNK])
                    .eq("active", True)
                    .gte("ends_at", now_iso)
                    .execute()
                    .data
                    or []
                )
            return rows
        except Exception as exc:  # noqa: BLE001
            logger.info("badge boost lookup (%s) failed: %s", cols, exc)
    return []


def paid_placements(client: Any, ids: list[str]) -> dict[str, str]:
    """listing id -> 'sponsored' | 'boosted' for every listing with an active paid placement."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    out: dict[str, str] = {}
    for row in _boost_rows(client, ids, datetime.now(timezone.utc).isoformat()):
        lid = str(row.get("listing_id") or "")
        if not lid:
            continue
        kind = "sponsored" if row.get("kind") == "sponsored" else "boosted"
        if out.get(lid) != "sponsored":
            out[lid] = kind
    return out


def trending_ids(client: Any) -> set[str]:
    try:
        from feed.service import current_snapshot

        return current_snapshot(client).trending()
    except Exception as exc:  # noqa: BLE001
        logger.info("trending set unavailable: %s", exc)
        return set()


def card_statuses(
    client: Any,
    rows: list[dict[str, Any]],
    *,
    placements: dict[str, str] | None = None,
    trending: set[str] | None = None,
) -> dict[str, str | None]:
    """rows need id, item_type, listing_meta and first_posted_at/created_at."""
    ids = [str(r.get("id")) for r in rows if r.get("id")]
    paid = paid_placements(client, ids)
    hot = trending_ids(client) if trending is None else trending
    now = datetime.now(timezone.utc)
    out: dict[str, str | None] = {}
    for row in rows:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        slot = (placements or {}).get(pid, "organic")
        out[pid] = visible_status(
            placement=slot if slot in ("sponsored", "boosted") else paid.get(pid, slot),
            item_type=row.get("item_type"),
            listing_meta=row.get("listing_meta"),
            posted_at=row.get("first_posted_at") or row.get("created_at"),
            now=now,
            trending=pid in hot,
        )
    return out
