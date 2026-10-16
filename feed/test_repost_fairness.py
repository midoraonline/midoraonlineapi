from datetime import datetime, timedelta, timezone

from feed import service
from shop import listing_actions as LA


def test_fresh_items_are_spread_not_pinned():
    ranked = [f"r{i}" for i in range(12)]
    out = service.interleave_fresh(ranked, ["new1", "new2"])
    assert out[:3] == ["r0", "r1", "r2"]
    assert out.index("new1") == 3 and out.index("new2") == 9
    assert len(out) == 14


def test_leftover_fresh_goes_last():
    assert service.interleave_fresh(["a"], ["n1", "n2"]) == ["a", "n1", "n2"]


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Q(self.rows)


def test_repost_rate_limits_still_apply():
    now = datetime.now(timezone.utc)
    rows = [{"listing_id": "x", "created_at": (now - timedelta(hours=1)).isoformat()}]
    listing = {"id": "x", "status": "active", "is_published": True}
    st = LA.repost_status(_DB(rows), listing, "u", {"reposts_per_day": 1, "repost_cooldown_hours": 12})
    assert not st["can_repost"] and "reposts" in st["reason"]
    st = LA.repost_status(_DB(rows), listing, "u", {"reposts_per_day": 3, "repost_cooldown_hours": 12})
    assert not st["can_repost"] and st["next_allowed_at"]
