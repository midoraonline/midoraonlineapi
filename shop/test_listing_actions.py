def test_boost_info_describe():
    from shop.boost_info import describe

    info = describe({"id": "b", "listing_id": "l", "shop_id": "s", "source": "admin", "score_bonus": 50,
                     "ends_at": "2026-10-19T11:00:00+00:00", "boost_plans": {"name": "3 days"}})
    assert info["package_name"] == "3 days" and info["scope"] == "shop" and info["source_label"] == "Admin"
    custom = describe({"listing_id": "l", "source": "plan_allowance", "boost_plans": None})
    assert custom["package_name"] == "Custom" and custom["scope"] == "listing" and custom["source_label"] == "Plan"


def test_invalidate_feed_caches_marks_snapshot_stale(monkeypatch):
    from feed import boost_slots, invalidate

    calls = []

    class Q:
        def __init__(self, t):
            self.t = t

        def update(self, p):
            calls.append((self.t, "update", p))
            return self

        def delete(self):
            calls.append((self.t, "delete", None))
            return self

        def eq(self, *a):
            return self

        def execute(self):
            return type("R", (), {"data": []})

    class C:
        def table(self, t):
            return Q(t)

    monkeypatch.setattr(boost_slots, "clear_cache", lambda: calls.append(("boosts", None, None)))
    invalidate.invalidate_feed_caches(C(), notify_next=False)
    assert ("boosts", None, None) in calls
    assert any(t == "feed_rank_snapshots" and op == "update" for t, op, _ in calls)
    assert not any(op == "delete" for _, op, _ in calls)


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
