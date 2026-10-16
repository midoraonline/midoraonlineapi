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


def test_invalidate_feed_caches_marks_user_caches_stale(monkeypatch):
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

    monkeypatch.setattr(snapshots, "clear_memory", lambda: calls.append(("mem", None)))
    invalidate.invalidate_feed_caches(C(), notify_next=False)
    assert ("user_feed_cache", {"refreshed_at": invalidate.STALE_AT}) in calls
    assert ("feed_rank_snapshots", "delete") in calls
    assert ("mem", None) in calls
