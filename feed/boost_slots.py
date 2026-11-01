from __future__ import annotations

import logging
import time
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


def clear_cache() -> None:
    _cache.update({"at": 0.0, "rows": None})


def slot_every() -> int:
    return max(2, int(C.BLOCK_SIZE))


def slot_phase() -> int:
    return max(1, min(int(C.PAID_SLOT_POSITION), slot_every()))


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


def organic_offset(page: int, limit: int, every: int, phase: int, unique: int) -> int:
    used = sum(len(page_slots(p, limit, every, phase, unique)) for p in range(1, page))
    return max(0, (page - 1) * limit - used)


def _load_rows(client: Any) -> list[dict[str, Any]]:
    from feed import signals as sig

    now_iso = datetime.now(timezone.utc).isoformat()
    r = (
        client.table("listing_boosts")
        .select("listing_id,score_bonus")
        .eq("active", True)
        .gte("ends_at", now_iso)
        .limit(2000)
        .execute()
    )
    bonus: dict[str, float] = {}
    for row in r.data or []:
        lid = str(row.get("listing_id") or "")
        if lid:
            bonus[lid] = max(bonus.get(lid, 0.0), float(row.get("score_bonus") or 0))
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


def paid_rotation(client: Any, plan: StagePlan) -> list[str]:
    if not plan.paid:
        return []
    return rotation(active_boosts(client))


def compose_page(
    organic: list[str],
    boost_seq: list[str],
    *,
    page: int,
    limit: int,
    every: int | None = None,
    phase: int | None = None,
) -> tuple[list[str], bool, set[str]]:
    every = every or slot_every()
    phase = phase or slot_phase()
    start = max(0, (page - 1) * limit)
    if not boost_seq:
        return organic[start : start + limit], start + limit < len(organic), set()
    unique = len(set(boost_seq))
    slots = set(page_slots(page, limit, every, phase, unique))
    k = slots_before(start, every, phase)
    o = organic_offset(page, limit, every, phase, unique)
    preview = set(organic[o : o + limit])
    out: list[str] = []
    paid: set[str] = set()
    n = len(boost_seq)
    for g in range(start + 1, start + limit + 1):
        if g in slots:
            pick = next(
                (boost_seq[(k + j) % n] for j in range(n) if boost_seq[(k + j) % n] not in paid | preview),
                None,
            )
            k += 1
            if pick is not None:
                out.append(pick)
                paid.add(pick)
                continue
        while o < len(organic) and organic[o] in paid:
            o += 1
        if o >= len(organic):
            break
        out.append(organic[o])
        o += 1
    return out, o < len(organic), paid
