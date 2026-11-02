from __future__ import annotations

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


def _first(pool: list[Entry], keeps: list[Keep], counts: dict[str, int], last_shop: str) -> Entry | None:
    for tier in range(3):
        for keep in keeps:
            for entry in pool:
                if not keep(entry):
                    continue
                shop = entry["shop"]
                if shop and tier == 0 and shop == last_shop:
                    continue
                if shop and tier < 2 and counts.get(shop, 0) >= C.SELLER_CAP_PER_BLOCK:
                    continue
                return entry
    return None


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


def place(entries: list[Entry], plan: StagePlan) -> list[Entry]:
    remaining = sorted(entries, key=lambda e: e["score"], reverse=True)
    explore_at = exploration_positions(plan.exploration_per_block)
    targets = lane_targets() if plan.type_mix else {}
    out: list[Entry] = []
    while remaining and len(out) < C.RANKED_MAX:
        slot = len(out) % C.BLOCK_SIZE + 1
        block = out[len(out) - slot + 1 :]
        counts: dict[str, int] = {}
        for e in block:
            counts[e["shop"]] = counts.get(e["shop"], 0) + 1
        last_shop = out[-1]["shop"] if out else ""
        pick, mode = None, "organic"
        if slot in explore_at:
            trial = sorted(
                (e for e in remaining if _explorable(e)),
                key=lambda e: e["quality"] + e["fresh"],
                reverse=True,
            )
            pick = _first(trial, [lambda _e: True], counts, last_shop)
            mode = "exploration" if pick else mode
        if pick is None:
            pick = _first(remaining, _lane_keeps(block, targets, remaining), counts, last_shop)
        if pick is None:
            break
        remaining.remove(pick)
        out.append({**pick, "mode": mode})
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
