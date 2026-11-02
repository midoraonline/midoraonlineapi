from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from feed import config as C
from feed.quality import listing_quality
from feed.scoring import HIDDEN_FRAUD
from feed.stages import StagePlan

logger = logging.getLogger(__name__)

_TTL_S = 60.0
MAX_BOOSTS = 300
_PRODUCT_COLS = "id,shop_id,category,title,description,image_urls,price_ugx,item_type,listing_meta,location_name"
_cache: dict[str, Any] = {"at": 0.0, "rows": None}
KINDS = ("sponsored", "boost")
PLACEMENT = {"sponsored": "sponsored", "boost": "boosted"}


@dataclass(frozen=True)
class PaidLane:
    placement: str
    seq: list[str]
    phase: int


def kind_of(row: dict[str, Any]) -> str:
    return "sponsored" if row.get("kind") == "sponsored" else "boost"


def clear_cache() -> None:
    _cache.update({"at": 0.0, "rows": None})


def slot_every() -> int:
    return max(2, int(C.BLOCK_SIZE))


def slot_phase(kind: str = "boost") -> int:
    every = slot_every()
    boost = max(1, min(int(C.PAID_SLOT_POSITION), every))
    if kind != "sponsored":
        return boost
    sponsored = max(1, min(int(C.SPONSORED_SLOT_POSITION), every))
    return sponsored if sponsored != boost else boost % every + 1


def is_slot(position: int, every: int, phase: int) -> bool:
    return position >= phase and (position - phase) % every == 0


def slots_before(n: int, every: int, phase: int) -> int:
    if n < phase:
        return 0
    return (n - phase) // every + 1


def page_slots(page: int, limit: int, every: int, phase: int, unique: int) -> list[int]:
    start = max(0, (page - 1) * limit)
    hits = [g for g in range(start + 1, start + limit + 1) if is_slot(g, every, phase)]
    return hits[: max(0, unique)]


def _active_boost_rows(client: Any, now_iso: str) -> list[dict[str, Any]]:
    try:
        r = (
            client.table("listing_boosts")
            .select("listing_id,score_bonus,kind")
            .eq("active", True)
            .gte("ends_at", now_iso)
            .limit(2000)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("boost kind unavailable (apply 064): %s", exc)
        r = (
            client.table("listing_boosts")
            .select("listing_id,score_bonus")
            .eq("active", True)
            .gte("ends_at", now_iso)
            .limit(2000)
            .execute()
        )
    return r.data or []


def _load_rows(client: Any) -> list[dict[str, Any]]:
    from feed import signals as sig

    now_iso = datetime.now(timezone.utc).isoformat()
    bonus: dict[str, float] = {}
    kinds: dict[str, str] = {}
    for row in _active_boost_rows(client, now_iso):
        lid = str(row.get("listing_id") or "")
        if lid:
            bonus[lid] = max(bonus.get(lid, 0.0), float(row.get("score_bonus") or 0))
            if kind_of(row) == "sponsored" or lid not in kinds:
                kinds[lid] = kind_of(row)
    if not bonus:
        return []
    ids = sorted(bonus, key=lambda i: -bonus[i])[:MAX_BOOSTS]
    products: list[dict[str, Any]] = []
    for i in range(0, len(ids), 150):
        pr = (
            client.table("products")
            .select(_PRODUCT_COLS)
            .in_("id", ids[i : i + 150])
            .eq("status", "active")
            .eq("is_published", True)
            .execute()
        )
        products.extend(p for p in (pr.data or []) if listing_quality(p) >= C.PAID_MIN_QUALITY)
    shop_meta = sig.collect_shop_meta(client, list({str(p["shop_id"]) for p in products if p.get("shop_id")}))
    by_shop = {str(p["id"]): str(p.get("shop_id") or "") for p in products}
    fraud = sig.collect_fraud_severity(client, list(by_shop), by_shop, shop_meta)
    rows = [
        {
            "id": str(p["id"]),
            "shop_id": str(p.get("shop_id") or ""),
            "category": (p.get("category") or "").strip(),
            "score_bonus": bonus[str(p["id"])],
            "kind": kinds.get(str(p["id"]), "boost"),
        }
        for p in products
        if fraud.get(str(p["id"]), "low") not in HIDDEN_FRAUD
    ]
    rows.sort(key=lambda x: (-x["score_bonus"], x["id"]))
    return rows


def active_boosts(client: Any) -> list[dict[str, Any]]:
    if _cache["rows"] is not None and time.monotonic() - _cache["at"] < _TTL_S:
        return _cache["rows"]
    try:
        rows = _load_rows(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boost slots: load failed: %s", exc)
        rows = []
    _cache.update({"at": time.monotonic(), "rows": rows})
    return rows


def rotation(boosts: Iterable[dict[str, Any]]) -> list[str]:
    items = list(boosts)
    if not items:
        return []
    weights = {b["id"]: max(1, round(float(b.get("score_bonus") or 0) / 25.0)) for b in items}
    seq: list[str] = []
    for r in range(max(weights.values())):
        seq.extend(b["id"] for b in items if weights[b["id"]] > r)
    return seq


def paid_rotation(client: Any, plan: StagePlan, kind: str = "boost") -> list[str]:
    if not plan.paid:
        return []
    return rotation(b for b in active_boosts(client) if kind_of(b) == kind)


def paid_lanes(client: Any, plan: StagePlan) -> list[PaidLane]:
    """Sponsored and Boosted rank in separate queues, one slot each per block."""
    if not plan.paid:
        return []
    rows = active_boosts(client)
    return [
        PaidLane(PLACEMENT[kind], rotation(b for b in rows if kind_of(b) == kind), slot_phase(kind))
        for kind in KINDS
    ]


def organic_offset_for(page: int, limit: int, every: int, lanes: list[PaidLane]) -> int:
    used = sum(
        len(page_slots(p, limit, every, lane.phase, len(set(lane.seq))))
        for p in range(1, page)
        for lane in lanes
        if lane.seq
    )
    return max(0, (page - 1) * limit - used)


def compose_paid(
    organic: list[str],
    lanes: list[PaidLane],
    *,
    page: int,
    limit: int,
    every: int | None = None,
) -> tuple[list[str], bool, dict[str, str]]:
    """Merge paid lanes into the organic order. Returns page ids, has_more, id -> placement."""
    every = every or slot_every()
    lanes = [lane for lane in lanes if lane.seq]
    start = max(0, (page - 1) * limit)
    if not lanes:
        return organic[start : start + limit], start + limit < len(organic), {}
    slots: dict[int, PaidLane] = {}
    for lane in lanes:
        for g in page_slots(page, limit, every, lane.phase, len(set(lane.seq))):
            slots.setdefault(g, lane)
    turn = {lane.placement: slots_before(start, every, lane.phase) for lane in lanes}
    o = organic_offset_for(page, limit, every, lanes)
    preview = set(organic[o : o + limit])
    out: list[str] = []
    paid: dict[str, str] = {}
    for g in range(start + 1, start + limit + 1):
        lane = slots.get(g)
        if lane is not None:
            n = len(lane.seq)
            k = turn[lane.placement]
            pick = next(
                (lane.seq[(k + j) % n] for j in range(n) if lane.seq[(k + j) % n] not in paid.keys() | preview),
                None,
            )
            turn[lane.placement] = k + 1
            if pick is not None:
                out.append(pick)
                paid[pick] = lane.placement
                continue
        while o < len(organic) and organic[o] in paid:
            o += 1
        if o >= len(organic):
            break
        out.append(organic[o])
        o += 1
    return out, o < len(organic), paid


def compose_page(
    organic: list[str],
    boost_seq: list[str],
    *,
    page: int,
    limit: int,
    every: int | None = None,
    phase: int | None = None,
) -> tuple[list[str], bool, set[str]]:
    lane = PaidLane("boosted", list(boost_seq), phase or slot_phase())
    out, more, paid = compose_paid(organic, [lane], page=page, limit=limit, every=every)
    return out, more, set(paid)
