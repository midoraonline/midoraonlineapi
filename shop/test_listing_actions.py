from feed import scoring
from feed.service import promote_boosted


def test_promote_boosted_lifts_by_points():
    ids = [f"p{i}" for i in range(20)]
    out = promote_boosted(ids, {"p15": {"score_bonus": 50}})
    assert out.index("p15") == 5
    assert len(out) == 20 and set(out) == set(ids)


def test_promote_boosted_clamps_to_top():
    out = promote_boosted(["a", "b", "c"], {"c": {"score_bonus": 500}})
    assert out[0] == "c"


def test_boost_bonus_is_literal_points():
    assert scoring.boost_bonus({"id": "x"}, {"x": {"score_bonus": 80}}) == 80
    assert scoring.boost_bonus({"id": "x"}, {"x": {"score_bonus": 0}}) == scoring.C.LISTING_BOOST_BONUS
    assert scoring.boost_bonus({"id": "x"}, {}) == 0
    assert scoring.boost_bonus({"id": "x"}, {"x": {"score_bonus": 9999}}) == 500


def test_boost_info_describe_and_card_fields():
    from shop.boost_info import card_fields, describe

    info = describe({"id": "b", "listing_id": "l", "shop_id": "s", "source": "admin", "score_bonus": 50,
                     "ends_at": "2026-10-19T11:00:00+00:00", "boost_plans": {"name": "3 days"}})
    assert info["package_name"] == "3 days" and info["scope"] == "shop" and info["source_label"] == "Admin"
    custom = describe({"listing_id": "l", "source": "plan_allowance", "boost_plans": None})
    assert custom["package_name"] == "Custom" and custom["scope"] == "listing" and custom["source_label"] == "Plan"
    f = card_fields(info)
    assert f["is_boosted"] and f["boosted"] and f["boost_ends_at"] == info["ends_at"]
    assert card_fields(None) == {"boosted": False, "is_boosted": False, "boost_ends_at": None, "boost": None}


def test_invalidate_feed_caches_keeps_user_caches(monkeypatch):
    from feed import invalidate, snapshots

    calls = []

    class Q:
        def __init__(self, t):
            self.t = t

        def update(self, p):
            calls.append((self.t, p))
            return self

        def delete(self):
            calls.append((self.t, "delete"))
            return self

        def gte(self, *a):
            return self

        def eq(self, *a):
            return self

        def execute(self):
            return type("R", (), {"data": []})

    class C:
        def table(self, t):
            return Q(t)

    from feed import boost_slots

    monkeypatch.setattr(snapshots, "clear_memory", lambda: calls.append(("mem", None)))
    monkeypatch.setattr(boost_slots, "clear_cache", lambda: calls.append(("boosts", None)))
    invalidate.invalidate_feed_caches(C(), notify_next=False)
    assert not any(t == "user_feed_cache" for t, _ in calls)
    assert ("boosts", None) in calls
    assert ("feed_rank_snapshots", "delete") in calls
    assert ("mem", None) in calls


def test_personalized_position_samples_cached_feeds(monkeypatch):
    from feed import boost_slots
    from shop import listing_actions as LA

    rows = [
        {"user_id": "owner", "ranked_ids": ["x"]},
        {"user_id": "u1", "ranked_ids": ["a", "x"]},
        {"user_id": "u2", "ranked_ids": ["a", "b", "c", "d", "e", "x"]},
        {"user_id": "u3", "ranked_ids": ["a"]},
    ]

    class Q:
        def __getattr__(self, n):
            return lambda *a, **k: self

        def execute(self):
            return type("R", (), {"data": rows})

    class DB:
        def table(self, t):
            return Q()

    monkeypatch.setattr(boost_slots, "active_boosts", lambda c: [])
    out = LA.personalized_position(DB(), "x", exclude_user_id="owner")
    assert out["sampled"] == 3 and out["in_feeds"] == 2
    assert out["best"] == 2 and out["median"] == 6
    assert "signed-in" in out["label"] and not out["boosted_slots"]


def test_shown_position_counts_boosted_slots(monkeypatch):
    from shop import listing_actions as LA
    from feed import config as C

    monkeypatch.setattr(C, "BOOST_SLOT_EVERY", 7)
    monkeypatch.setattr(C, "BOOST_SLOT_PHASE", 3)
    assert LA._shown_position(0, True) == 1
    assert LA._shown_position(2, True) == 4  # position 3 is a boosted slot
    assert LA._shown_position(2, False) == 3


def test_frequency_caps_never_hide_own_ads():
    from feed import frequency as F

    F._tier_cache.clear()
    data = {
        "listing_impressions": [{"listing_id": "mine"}, {"listing_id": "theirs"}],
        "products": [{"id": "mine", "shop_id": "s_me"}, {"id": "theirs", "shop_id": "s_x"}],
        "shops": [{"id": "s_me", "owner_id": "me"}, {"id": "s_x", "owner_id": "x"}],
        "users": [{"id": "me", "plan_tier": "basic"}, {"id": "x", "plan_tier": "basic"}],
    }

    class Q:
        def __init__(self, t):
            self.t = t
            self.owner = None

        def eq(self, k, v):
            if k == "owner_id":
                self.owner = v
            return self

        def __getattr__(self, n):
            return lambda *a, **k: self

        def execute(self):
            rows = data.get(self.t, [])
            if self.owner:
                rows = [r for r in rows if r.get("owner_id") == self.owner]
            return type("R", (), {"data": rows})

    class DB:
        def table(self, t):
            return Q(t)

    assert F.capped_listing_ids(DB(), buyer_id="me", session_id=None) == {"theirs"}
