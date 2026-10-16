from datetime import datetime, timezone

from feed import frequency as F
from feed import service


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return type("R", (), {"data": self.rows})


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


def test_day_starts_at_midnight_kampala():
    now = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)  # 04:00 EAT
    assert F.day_start_utc(now) == datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc)


def test_cap_for_plan_values():
    basic = {"frequency_per_day": 1, "frequency_per_day_boosted": 0}
    premium = {"frequency_per_day": 0, "frequency_per_day_boosted": 5}
    assert F.cap_for(basic, False) == 1
    assert F.cap_for(basic, True) == 1  # no boosted cap -> organic cap
    assert F.cap_for(premium, False) == 0  # unlimited
    assert F.cap_for(premium, True) == 5


def test_capped_listing_ids_uses_impressions_and_owner_plan(monkeypatch):
    F._tier_cache.clear()
    db = _DB({
        "listing_impressions": [{"listing_id": "a"}, {"listing_id": "b"}, {"listing_id": "b"}, {"listing_id": "c"}],
        "products": [{"id": "a", "shop_id": "s1"}, {"id": "b", "shop_id": "s2"}, {"id": "c", "shop_id": "s2"}],
        "shops": [{"id": "s1", "owner_id": "u1"}, {"id": "s2", "owner_id": "u2"}],
        "users": [{"id": "u1", "plan_tier": "basic"}, {"id": "u2", "plan_tier": "standard"}],
    })
    capped = F.capped_listing_ids(db, buyer_id="v", session_id=None, boosted_ids={"b", "c"})
    # basic: 1/day organic -> a capped; standard boosted 2/day -> b (2) capped, c (1) not
    assert capped == {"a", "b"}


def test_page_build_drops_capped(monkeypatch):
    monkeypatch.setattr(service, "get_supabase_admin", lambda: object())
    monkeypatch.setattr(service.boost_slots, "active_boosts", lambda c: [])
    monkeypatch.setattr(F, "capped_listing_ids", lambda *a, **k: {"p1"})
    ids, _ = service._page_ids_from_ranked(
        ["p0", "p1", "p2"], page=1, limit=3, exclude_ids=None, user_id="u", session_id=None
    )
    assert ids == ["p0", "p2"]
