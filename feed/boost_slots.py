"""Guaranteed boosted slots, applied when a page is read.

Boosted ads keep their organic rank (the score bonus is part of the score);
on top of that every `BOOST_SLOT_EVERY` positions is reserved for a boosted
ad drawn from ALL active boosts, not just the viewer's candidates.

- Signed-in viewers get boosts that match their taste (cosine of the cached
  preference vector >= BOOST_RELEVANCE_MIN_SIMILARITY) or one of their
  categories; if none match, every active boost is eligible (fallback).
- Guests get every active boost.
- Slots rotate through boosts weighted by score_bonus (every 25 points = one
  more turn), and a boost is never repeated on a page or shown in a slot
  when it is already on the page organically.

Slot positions are global (`phase`, `phase+every`, ...), so page N always
knows how many organic items earlier pages used and pagination stays stable.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from feed import config as C
from feed.embeddings import cosine_similarity

logger = logging.getLogger(__name__)

_TTL_S = 60.0
MAX_BOOSTS = 300
_cache: dict[str, Any] = {"at": 0.0, "rows": None, "emb": None}


def clear_cache() -> None:
    _cache.update({"at": 0.0, "rows": None, "emb": None})


def slot_every() -> int:
    return max(2, int(getattr(C, "BOOST_SLOT_EVERY", 7) or 7))


def slot_phase() -> int:
    return max(1, int(getattr(C, "BOOST_SLOT_PHASE", 3) or 3))


def is_slot(position: int, every: int, phase: int) -> bool:
    """`position` is 1-based in the whole feed."""
    return position >= phase and (position - phase) % every == 0


def slots_before(n: int, every: int, phase: int) -> int:
    """Slot positions within 1..n."""
    if n < phase:
        return 0
    return (n - phase) // every + 1


def page_slots(page: int, limit: int, every: int, phase: int, unique: int) -> list[int]:
    """Slot positions used on `page`: at most one per distinct boost, so a
    page never needs to repeat a boost (and never leaves a slot unfilled)."""
    start = max(0, (page - 1) * limit)
    hits = [g for g in range(start + 1, start + limit + 1) if is_slot(g, every, phase)]
    return hits[: max(0, unique)]


def organic_offset(page: int, limit: int, every: int, phase: int, unique: int) -> int:
    """How many organic items pages 1..page-1 used."""
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
            sb = float(row.get("score_bonus") or 0) or float(C.LISTING_BOOST_BONUS)
            bonus[lid] = max(bonus.get(lid, 0.0), sb)
    if not bonus:
        return []
    ids = sorted(bonus, key=lambda i: -bonus[i])[:MAX_BOOSTS]
    products: list[dict[str, Any]] = []
    for i in range(0, len(ids), 150):
        pr = (
            client.table("products")
            .select("id,shop_id,category")
            .in_("id", ids[i : i + 150])
            .eq("status", "active")
            .eq("is_published", True)
            .execute()
        )
        products.extend(pr.data or [])
    shop_ids = list({str(p["shop_id"]) for p in products if p.get("shop_id")})
    shop_meta = sig.collect_shop_meta(client, shop_ids)
    by_shop = {str(p["id"]): str(p.get("shop_id") or "") for p in products}
    fraud = sig.collect_fraud_severity(client, list(by_shop), by_shop, shop_meta)
    rows = []
    for p in products:
        pid = str(p["id"])
        if C.FRAUD_SEVERITY_PENALTY.get(fraud.get(pid, "low"), 0.0) is None:
            continue  # critical fraud never gets a paid slot
        rows.append({
            "id": pid,
            "shop_id": str(p.get("shop_id") or ""),
            "category": (p.get("category") or "").strip(),
            "score_bonus": bonus[pid],
        })
    rows.sort(key=lambda x: (-x["score_bonus"], x["id"]))
    return rows


def active_boosts(client: Any) -> list[dict[str, Any]]:
    """All live boosted listings (id, shop_id, category, score_bonus), cached 60s."""
    if _cache["rows"] is not None and time.monotonic() - _cache["at"] < _TTL_S:
        return _cache["rows"]
    try:
        rows = _load_rows(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boost slots: load failed: %s", exc)
        rows = []
    _cache.update({"at": time.monotonic(), "rows": rows, "emb": None})
    return rows


def _embeddings(client: Any, ids: list[str]) -> dict[str, list[float]]:
    if _cache.get("emb") is None:
        from feed.service import _load_embeddings_for_ids

        _cache["emb"] = _load_embeddings_for_ids(client, ids) if ids else {}
    return _cache["emb"]


def rotation(boosts: Iterable[dict[str, Any]]) -> list[str]:
    """Weighted round-robin: a 50-point boost gets 2 turns, 75 gets 3."""
    items = list(boosts)
    if not items:
        return []
    weights = {b["id"]: max(1, round(float(b.get("score_bonus") or 0) / 25.0)) for b in items}
    seq: list[str] = []
    for r in range(max(weights.values())):
        seq.extend(b["id"] for b in items if weights[b["id"]] > r)
    return seq


def relevant(
    boosts: list[dict[str, Any]],
    *,
    categories: set[str] | None,
    taste: dict[str, float] | None,
    min_similarity: float | None = None,
) -> list[dict[str, Any]]:
    """Boosts that match the viewer; all boosts when nothing matches."""
    thr = float(C.BOOST_RELEVANCE_MIN_SIMILARITY if min_similarity is None else min_similarity)
    cats = {c for c in (categories or set()) if c}
    taste = taste or {}
    hits = [b for b in boosts if (b["category"] and b["category"] in cats) or taste.get(b["id"], 0.0) >= thr]
    return hits or list(boosts)


def boosts_for_viewer(
    client: Any,
    *,
    user_vector: list[float] | None,
    categories: set[str] | None,
    signed_in: bool,
) -> list[str]:
    boosts = active_boosts(client)
    if not boosts:
        return []
    if not signed_in:
        return rotation(boosts)
    taste: dict[str, float] = {}
    if user_vector:
        emb = _embeddings(client, [b["id"] for b in boosts])
        for b in boosts:
            if b["id"] in emb:
                taste[b["id"]] = max(0.0, cosine_similarity(user_vector, emb[b["id"]]))
    return rotation(relevant(boosts, categories=categories, taste=taste))


def compose_page(
    organic: list[str],
    boost_seq: list[str],
    *,
    page: int,
    limit: int,
    every: int | None = None,
    phase: int | None = None,
) -> tuple[list[str], bool]:
    """Slice `organic` for this page with boosted slots woven in.

    Returns (ids, has_more). Without boosts it is a plain slice.
    """
    every = every or slot_every()
    phase = phase or slot_phase()
    start = max(0, (page - 1) * limit)
    if not boost_seq:
        return organic[start : start + limit], start + limit < len(organic)
    unique = len(set(boost_seq))
    slots = set(page_slots(page, limit, every, phase, unique))
    k = slots_before(start, every, phase)  # rotation offset, so pages rotate
    o = organic_offset(page, limit, every, phase, unique)
    preview = set(organic[o : o + limit])
    out: list[str] = []
    on_page: set[str] = set()
    n = len(boost_seq)
    for g in range(start + 1, start + limit + 1):
        if g in slots:
            pick = None
            for j in range(n):
                cand = boost_seq[(k + j) % n]
                if cand not in on_page and cand not in preview:
                    pick = cand
                    break
            k += 1
            if pick is not None:
                out.append(pick)
                on_page.add(pick)
                continue
        while o < len(organic) and organic[o] in on_page:
            o += 1
        if o >= len(organic):
            break
        out.append(organic[o])
        on_page.add(organic[o])
        o += 1
    return out, o < len(organic)
