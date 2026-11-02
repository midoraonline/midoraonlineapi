from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from feed import config as C
from feed.stages import StagePlan

logger = logging.getLogger(__name__)

CURRENT_KEY = "home"
PREVIOUS_KEY = "home:prev"
_STALE_AT = "1970-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class Snapshot:
    version: str
    plan: StagePlan
    items: list[dict[str, Any]] = field(default_factory=list)

    def ids(self) -> list[str]:
        return [i["id"] for i in self.items]

    def trending(self) -> set[str]:
        return {i["id"] for i in self.items if i.get("trending")}

    def payload(self) -> dict[str, Any]:
        return {"plan": self.plan.as_dict(), "items": self.items}

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> "Snapshot | None":
        if not row or not row.get("version"):
            return None
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return cls(
            version=str(row["version"]),
            plan=StagePlan.from_dict(payload.get("plan")),
            items=[i for i in items if isinstance(i, dict) and i.get("id")],
        )


_lock = threading.Lock()
_memory: dict[str, tuple[float, Snapshot]] = {}


def clear_memory() -> None:
    with _lock:
        hit = _memory.pop(CURRENT_KEY, None)
        if hit:
            _memory[PREVIOUS_KEY] = hit


def _remember(key: str, snap: Snapshot, at: float) -> None:
    with _lock:
        _memory[key] = (at, snap)


def _fresh(stored_at: float) -> bool:
    return time.monotonic() - stored_at <= C.SNAPSHOT_TTL_SECONDS


def _age_seconds(raw: Any) -> float | None:
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def _rows(client: Any) -> dict[str, dict[str, Any]]:
    try:
        r = (
            client.table("feed_rank_snapshots")
            .select("snapshot_key,version,payload,refreshed_at")
            .in_("snapshot_key", [CURRENT_KEY, PREVIOUS_KEY])
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("feed snapshot read skipped: %s", exc)
        return {}
    return {str(row.get("snapshot_key")): row for row in (r.data or [])}


def current(client: Any) -> Snapshot | None:
    with _lock:
        hit = _memory.get(CURRENT_KEY)
    if hit and _fresh(hit[0]):
        return hit[1]
    row = _rows(client).get(CURRENT_KEY)
    age = _age_seconds(row.get("refreshed_at")) if row else None
    if age is None or not 0 <= age <= C.SNAPSHOT_TTL_SECONDS:
        return None
    snap = Snapshot.from_row(row)
    if snap:
        _remember(CURRENT_KEY, snap, time.monotonic() - age)
    return snap


def by_version(client: Any, version: str) -> Snapshot | None:
    with _lock:
        cached = [snap for _, snap in _memory.values() if snap.version == version]
    if cached:
        return cached[0]
    for row in _rows(client).values():
        snap = Snapshot.from_row(row)
        if snap and snap.version == version:
            _remember(PREVIOUS_KEY, snap, time.monotonic())
            return snap
    return None


def save(client: Any, snap: Snapshot) -> None:
    with _lock:
        previous = _memory.get(CURRENT_KEY)
    if previous and previous[1].version != snap.version:
        _remember(PREVIOUS_KEY, previous[1], previous[0])
    _remember(CURRENT_KEY, snap, time.monotonic())
    try:
        old = Snapshot.from_row(_rows(client).get(CURRENT_KEY))
        rows = [{
            "snapshot_key": CURRENT_KEY,
            "version": snap.version,
            "payload": snap.payload(),
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        }]
        if old and old.version != snap.version:
            rows.append({
                "snapshot_key": PREVIOUS_KEY,
                "version": old.version,
                "payload": old.payload(),
                "refreshed_at": _STALE_AT,
            })
        client.table("feed_rank_snapshots").upsert(rows, on_conflict="snapshot_key").execute()
    except Exception as exc:  # noqa: BLE001
        logger.info("feed snapshot write skipped: %s", exc)


def invalidate(client: Any) -> None:
    clear_memory()
    try:
        client.table("feed_rank_snapshots").update({"refreshed_at": _STALE_AT}).eq(
            "snapshot_key", CURRENT_KEY
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.info("feed snapshot invalidate skipped: %s", exc)
