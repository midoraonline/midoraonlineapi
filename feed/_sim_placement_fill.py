"""One-shot simulation: placement fill under a 3-shop concentrated inventory.

Run from repo root:
  PYTHONPATH=. python3 feed/_sim_placement_fill.py
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

# Avoid pulling DB / httpx just to exercise place().
for name in ("db", "db.supabase", "feed.signals"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["db.supabase"].Client = object  # type: ignore[attr-defined]
sys.modules["feed.signals"].parse_timestamp = lambda _x: None  # type: ignore[attr-defined]

from feed.placement import place  # noqa: E402


def _entry(pid: str, shop: str, score: float) -> dict:
    return {"product": {"id": pid, "shop_id": shop}, "score": score}


def main() -> None:
    pools = {
        "super_boost": [],
        "sponsored": [],
        "boosted": [],
        "premium_store": [],
        "fresh": [],
        "exploration": [],
        "organic": [
            _entry(f"p{i}", f"shop{i % 3}", 100.0 - i * 0.1) for i in range(36)
        ],
    }
    limit = 36
    out = place(pools, limit=limit, now=datetime.now(timezone.utc))
    assert len(out) == limit, f"expected {limit} placed, got {len(out)}"
    first12 = [str(x["product"]["shop_id"]) for x in out[:12]]
    assert len(set(first12)) >= 2, f"first12 not diverse: {first12}"
    print(f"OK: placed={len(out)} shops_in_first12={sorted(set(first12))}")


if __name__ == "__main__":
    main()
