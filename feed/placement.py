from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from feed import config as C
from feed.stages import LANES, StagePlan, lane_targets

Entry = dict[str, Any]
Keep = Callable[[Entry], bool]


def exploration_positions(per_block: int) -> frozenset[int]:
    positions: set[int] = set()
    paid = {C.PAID_SLOT_POSITION, C.SPONSORED_SLOT_POSITION}
    for k in range(max(per_block, 0)):
        pos = round(C.BLOCK_SIZE * (k + 1) / (per_block + 1)) + 1
        while pos in paid:
            pos += 1
        positions.add(pos)
    return frozenset(positions)


def _explorable(entry: Entry) -> bool:
    return entry["quality"] >= C.EXPLORATION_MIN_QUALITY and entry["impressions"] < C.EXPLORATION_MAX_IMPRESSIONS


LADDER = (
    "ranked",
    "quality_floor_relaxed",
    "type_run_relaxed",
    "subcategory_run_relaxed",
    "seller_gap_relaxed",
    "seller_cap_3",
    "seller_cap_4",
    "seller_cap_open",
)


@dataclass
class _Ctx:
    block: list[Entry]
    recent: list[Entry]
    first_block: bool
    lanes_left: set[str]
    cats_left: set[str]
    has_quality: bool


def _run(block: list[Entry], key: str, size: int) -> str | None:
    tail = block[-size:] if size > 0 else []
    if len(tail) < size or not tail:
        return None
    first = tail[0].get(key) or ""
    return first if first and all((e.get(key) or "") == first for e in tail) else None


def _allowed(entry: Entry, level: int, ctx: _Ctx) -> bool:
    shop = entry["shop"]
    if shop:
        cap = C.SELLER_CAP_PER_BLOCK if level < 5 else 3 if level == 5 else 4 if level == 6 else None
        if cap is not None and sum(1 for e in ctx.block if e["shop"] == shop) >= cap:
            return False
        if level < 4 and ctx.recent and ctx.recent[-1]["shop"] == shop:
            return False
    if level < 1 and ctx.first_block and ctx.has_quality and entry["quality"] < C.FIRST_BLOCK_QUALITY_FLOOR:
        return False
    if level < 2:
        lane = _run(ctx.recent, "lane", C.MAX_SAME_TYPE_RUN)
        if lane and entry["lane"] == lane and ctx.lanes_left - {lane}:
            return False
    if level < 3:
        cat = _run(ctx.recent, "cat", C.MAX_SAME_SUBCATEGORY_RUN)
        if cat and entry.get("cat") == cat and ctx.cats_left - {cat}:
            return False
    return True


def _first(pool: list[Entry], keeps: list[Keep], ctx: _Ctx) -> tuple[Entry | None, str]:
    for level, reason in enumerate(LADDER):
        for keep in keeps:
            for entry in pool:
                if keep(entry) and _allowed(entry, level, ctx):
                    return entry, reason
    return None, ""


def _lane_keeps(block: list[Entry], targets: dict[str, int], remaining: list[Entry]) -> list[Keep]:
    if not targets:
        return [lambda _e: True]
    placed = {lane: sum(1 for e in block if e["lane"] == lane) for lane in LANES}
    supplied = {e["lane"] for e in remaining}
    order = sorted(
        (lane for lane in LANES if lane in supplied),
        key=lambda lane: (placed[lane] - targets[lane], LANES.index(lane)),
    )
    return [(lambda e, lane=lane: e["lane"] == lane) for lane in order]


def place(entries: list[Entry], plan: StagePlan, *, limit: int | None = None, offset: int = 0) -> list[Entry]:
    # `tier` (optional) ranks groups such as local/nearby/wider and not-recently-shown first;
    # score orders within a tier. Lane targets still apply across all tiers of the page.
    remaining = sorted(entries, key=lambda e: (e.get("tier", 0), -e["score"]))
    explore_at = exploration_positions(plan.exploration_per_block)
    targets = lane_targets() if plan.type_mix else {}
    cap = min(C.RANKED_MAX, limit) if limit is not None else C.RANKED_MAX
    out: list[Entry] = []
    while remaining and len(out) < cap:
        at = offset + len(out)
        slot = at % C.BLOCK_SIZE + 1
        ctx = _Ctx(
            block=out[max(0, len(out) - slot + 1) :],
            recent=out[-8:],
            first_block=at < C.BLOCK_SIZE,
            lanes_left={e["lane"] for e in remaining},
            cats_left={e.get("cat") or "" for e in remaining},
            has_quality=any(e["quality"] >= C.FIRST_BLOCK_QUALITY_FLOOR for e in remaining),
        )
        pick, mode, reason = None, "organic", ""
        if slot in explore_at:
            trial = sorted(
                (e for e in remaining if _explorable(e)),
                key=lambda e: e["quality"] + e["fresh"],
                reverse=True,
            )
            pick, reason = _first(trial, [lambda _e: True], ctx)
            mode = "exploration" if pick else mode
        if pick is None:
            pick, reason = _first(remaining, _lane_keeps(ctx.block, targets, remaining), ctx)
        if pick is None:
            break
        remaining.remove(pick)
        out.append({**pick, "mode": mode, "reason": reason})
    return out


def lift_followed(items: list[Entry], followed: set[str]) -> list[Entry]:
    if not followed or not any(i["shop"] in followed for i in items):
        return items
    out = list(items)
    for start in range(0, len(out), C.BLOCK_SIZE):
        slots = [i for i in range(start, min(start + C.BLOCK_SIZE, len(out))) if out[i]["mode"] == "organic"]
        block = [out[i] for i in slots]
        lifted = [e for e in block if e["shop"] in followed]
        others = [e for e in block if e["shop"] not in followed]
        # Followed cards move ahead of cheaper-scored cards; everyone else keeps the shared order.
        ranked: list[Entry] = []
        for entry in others:
            while lifted and lifted[0]["score"] + C.FOLLOWED_SHOP_BOOST >= entry["score"]:
                ranked.append(lifted.pop(0))
            ranked.append(entry)
        ranked.extend(lifted)
        for i, entry in zip(slots, ranked):
            out[i] = entry
        for a, b in zip(slots, slots[1:]):
            if b != a + 1 or not out[a]["shop"] or out[a]["shop"] != out[b]["shop"]:
                continue
            swap = next((c for c in slots if c > b and out[c]["shop"] != out[a]["shop"]), None)
            if swap is not None:
                out[b], out[swap] = out[swap], out[b]
    return out
