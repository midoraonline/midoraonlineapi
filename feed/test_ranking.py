from datetime import datetime, timedelta, timezone

from feed import boost_slots as B
from feed import config as C
from feed import placement as P
from feed import scoring, snapshots
from feed.quality import listing_quality
from feed.router import _decode_cursor
from feed.stages import StagePlan, lane_of, plan_for, stage_for
from feed.status import visible_status


def _entry(pid, shop, score, lane="product", quality=80.0, impressions=100, fresh=10.0):
    return {"id": pid, "shop": shop, "lane": lane, "score": score, "quality": quality,
            "impressions": impressions, "fresh": fresh}


def test_stage_thresholds_follow_growth_stages():
    assert [stage_for(n) for n in (0, 1, 49, 50, 199, 200, 499, 500, 999, 1000, 4999, 5000)] == [
        0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6,
    ]


def test_mvp_stage_has_no_paid_slots_exploration_or_type_mix():
    plan = plan_for({"product": 120, "service": 40, "opportunity": 30})
    assert plan.stage == 2
    assert not plan.paid and not plan.type_mix and plan.exploration_per_block == 0


def test_type_mix_needs_depth_in_every_lane():
    thin = plan_for({"product": 400, "service": 50, "opportunity": 4})
    deep = plan_for({"product": 400, "service": 50, "opportunity": 40})
    assert thin.stage == deep.stage == 3
    assert thin.paid and thin.exploration_per_block == 1
    assert not thin.type_mix and deep.type_mix


def test_lane_mapping():
    assert lane_of("job") == lane_of("opportunity") == "opportunity"
    assert lane_of("service") == "service"
    assert lane_of(None) == lane_of("property") == "product"


def test_quality_rewards_complete_listings():
    full = {"item_type": "product", "title": "Samsung Galaxy A54 128GB", "description": "x" * 90,
            "price_ugx": 900000, "image_urls": ["a.jpg", "b.jpg"], "location_name": "Ntinda", "category": "Phones"}
    bare = {"item_type": "product", "title": "Phone", "image_urls": [], "stock_quantity": 0}
    service = {"item_type": "service", "title": "Residential plumbing", "description": "y" * 90,
               "listing_meta": {"pricing_model": "quote", "service_areas": ["Kampala"], "availability": "Mon-Sat"},
               "image_urls": ["p.jpg"], "category": "Plumbing"}
    assert listing_quality(full) == 100
    assert listing_quality(bare) < C.QUALITY_MIN
    assert listing_quality(service) == 100


def test_freshness_uses_age_bands():
    now = datetime.now(timezone.utc)
    ago = lambda h: {"created_at": (now - timedelta(hours=h)).isoformat()}
    assert [scoring.freshness(ago(h), now) for h in (1, 10, 30, 80, 130, 200, 400, 800)] == [
        100, 90, 75, 60, 45, 30, 15, 5,
    ]


def test_medium_fraud_is_penalised():
    now = datetime.now(timezone.utc)
    args = dict(quality=80.0, shop_meta=None, impressions=0, eligible=10, stage=1, now=now)
    assert scoring.score({}, fraud="medium", **args) == scoring.score({}, fraud="low", **args) - C.FRAUD_MEDIUM_PENALTY


def test_placement_spreads_one_seller_and_never_repeats():
    entries = [_entry(f"a{i}", "big", 100 - i) for i in range(10)] + [_entry(f"b{i}", f"s{i}", 50 - i) for i in range(30)]
    out = P.place(entries, StagePlan(stage=1, eligible=40))
    ids = [e["id"] for e in out]
    assert len(ids) == len(set(ids)) == 40
    first_block = [e["shop"] for e in out[:20]]
    assert first_block.count("big") == C.SELLER_CAP_PER_BLOCK
    assert all(a != b or not a for a, b in zip(first_block, first_block[1:]))


def test_placement_relaxes_caps_when_supply_is_thin():
    entries = [_entry(f"a{i}", "only", 10 - i) for i in range(5)]
    out = P.place(entries, StagePlan(stage=1, eligible=5))
    assert [e["id"] for e in out] == [f"a{i}" for i in range(5)]


def test_type_mix_targets_lanes_in_each_block():
    entries = (
        [_entry(f"p{i}", f"ps{i}", 100 - i) for i in range(60)]
        + [_entry(f"s{i}", f"ss{i}", 10 - i * 0.1, lane="service") for i in range(30)]
        + [_entry(f"o{i}", f"os{i}", 5 - i * 0.1, lane="opportunity") for i in range(20)]
    )
    out = P.place(entries, StagePlan(stage=3, eligible=110, type_mix=True))
    lanes = [e["lane"] for e in out[:20]]
    assert lanes.count("product") == 12 and lanes.count("service") == 5 and lanes.count("opportunity") == 3


def test_exploration_slot_picks_underexposed_quality_listing():
    entries = [_entry(f"o{i}", f"s{i}", 100 - i) for i in range(30)]
    entries.append(_entry("hidden_gem", "new", 1, quality=95, impressions=0, fresh=90))
    plan = StagePlan(stage=3, eligible=31, exploration_per_block=1)
    out = P.place(entries, plan)
    slot = min(P.exploration_positions(1))
    assert out[slot - 1]["id"] == "hidden_gem" and out[slot - 1]["mode"] == "exploration"
    assert C.PAID_SLOT_POSITION not in P.exploration_positions(2)


def test_lift_followed_moves_within_block_only():
    items = [{"id": f"i{n}", "shop": f"s{n}", "score": 100 - n, "mode": "organic"} for n in range(40)]
    lifted = P.lift_followed(items, {"s5", "s30"})
    assert lifted[0]["id"] == "i5"
    assert lifted[20]["id"] == "i30"
    assert sorted(i["id"] for i in lifted) == sorted(i["id"] for i in items)
    assert P.lift_followed(items, set()) is items


def test_paid_lane_waits_for_paid_stage(monkeypatch):
    monkeypatch.setattr(B, "active_boosts", lambda c: [{"id": "b", "score_bonus": 50}])
    assert B.paid_rotation(None, StagePlan(stage=2, eligible=150)) == []
    assert B.paid_rotation(None, StagePlan(stage=3, eligible=250, paid=True)) == ["b", "b"]


def test_compose_places_one_paid_card_per_block():
    organic = [f"o{i}" for i in range(60)]
    p1, more1, paid1 = B.compose_page(organic, ["b1", "b2"], page=1, limit=40)
    assert p1[C.PAID_SLOT_POSITION - 1] == "b1" and p1[C.BLOCK_SIZE + C.PAID_SLOT_POSITION - 1] == "b2"
    assert paid1 == {"b1", "b2"} and len(p1) == len(set(p1)) == 40 and more1
    p2, _, _ = B.compose_page(organic, ["b1", "b2"], page=2, limit=40)
    flat = [i for i in p1 + p2 if i.startswith("o")]
    assert flat == organic[: len(flat)]


def test_compose_skips_boost_already_on_page():
    organic = ["b1"] + [f"o{i}" for i in range(30)]
    page, _, paid = B.compose_page(organic, ["b1"], page=1, limit=20)
    assert page.count("b1") == 1 and not paid


def test_rotation_weights_by_score_bonus():
    seq = B.rotation([{"id": "big", "score_bonus": 75}, {"id": "small", "score_bonus": 25}])
    assert seq.count("big") == 3 and seq.count("small") == 1


def test_sponsored_and_boosted_get_separate_slots(monkeypatch):
    monkeypatch.setattr(B, "active_boosts", lambda c: [
        {"id": "b1", "score_bonus": 25, "kind": "boost"},
        {"id": "s1", "score_bonus": 25, "kind": "sponsored"},
    ])
    lanes = B.paid_lanes(None, StagePlan(stage=3, eligible=250, paid=True))
    assert [(lane.placement, lane.seq) for lane in lanes] == [("sponsored", ["s1"]), ("boosted", ["b1"])]
    assert B.paid_lanes(None, StagePlan(stage=2, eligible=150)) == []
    organic = [f"o{i}" for i in range(80)]
    p1, _, paid = B.compose_paid(organic, lanes, page=1, limit=20)
    assert p1[C.PAID_SLOT_POSITION - 1] == "b1" and p1[C.SPONSORED_SLOT_POSITION - 1] == "s1"
    assert paid == {"b1": "boosted", "s1": "sponsored"} and len(p1) == 20
    p2, _, _ = B.compose_paid(organic, lanes, page=2, limit=20)
    flat = [i for i in p1 + p2 if i.startswith("o")]
    assert flat == organic[: len(flat)]


def test_sponsored_phase_never_collides_with_boost(monkeypatch):
    monkeypatch.setattr(C, "SPONSORED_SLOT_POSITION", C.PAID_SLOT_POSITION)
    assert B.slot_phase("sponsored") != B.slot_phase("boost")


def test_trending_picks_top_engaged_per_lane():
    from feed import trending

    now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    posted = (now - timedelta(hours=12)).isoformat()
    rows = [{"id": f"p{i}", "lane": "product", "posted_at": posted} for i in range(10)]
    rows.append({"id": "svc", "lane": "service", "posted_at": posted})
    hot = {"unique_viewers": 40, "likes": 5, "whatsapp_clicks": 3}
    warm = {"unique_viewers": 10, "likes": 1}
    cold = {"unique_viewers": 2, "whatsapp_clicks": 1}
    stats = {"p0": warm, "p1": hot, "p2": cold, "svc": warm}
    assert trending.pick(rows, stats, now) == {"p1", "svc"}
    assert not trending.qualifies({"unique_viewers": 50})
    assert trending.decayed(100, 0) > trending.decayed(100, 48)


def test_visible_status_priority():
    now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=2)).isoformat()
    soon = {"deadline": "2026-11-03"}
    assert visible_status(placement="boosted", item_type="opportunity", listing_meta=soon, posted_at=fresh, now=now) == "boosted"
    assert visible_status(placement="organic", item_type="job", listing_meta=soon, posted_at=fresh, now=now) == "closing_soon"
    assert visible_status(placement="organic", item_type="product", listing_meta=soon, posted_at=fresh, now=now) == "new"
    old = (now - timedelta(days=10)).isoformat()
    assert visible_status(placement="exploration", item_type="product", listing_meta={}, posted_at=old, now=now) is None
    assert visible_status(placement="sponsored", item_type="opportunity", listing_meta=soon, posted_at=fresh, now=now, trending=True) == "sponsored"
    assert visible_status(placement="organic", item_type="product", listing_meta={}, posted_at=fresh, now=now, trending=True) == "trending"
    assert visible_status(placement="organic", item_type="job", listing_meta=soon, posted_at=old, now=now, trending=True) == "closing_soon"


def test_popular_locations_skip_online_and_use_shop_place():
    from feed.locations import count_locations

    rows = [
        {"location_name": "Kampala", "shop_id": "a"},
        {"location_name": "Kampala", "shop_id": "b"},
        {"location_name": None, "shop_id": "c"},
        {"location_name": "Online", "shop_id": "d"},
    ]
    assert count_locations(rows, {"c": "Ntinda"}) == [{"name": "Kampala", "count": 2}, {"name": "Ntinda", "count": 1}]


def test_cursor_carries_snapshot_version():
    assert _decode_cursor("v:abc123:p:3") == (3, "abc123")
    assert _decode_cursor("p:2") == (2, None)
    assert _decode_cursor("v::p:2") == (None, None)
    assert _decode_cursor("junk") == (None, None)


def test_snapshot_versions_survive_invalidation(monkeypatch):
    class DB:
        def table(self, _t):
            raise RuntimeError("offline")

    snapshots._memory.clear()
    first = snapshots.Snapshot(version="v1", plan=StagePlan(stage=1, eligible=1), items=[{"id": "a"}])
    second = snapshots.Snapshot(version="v2", plan=StagePlan(stage=1, eligible=1), items=[{"id": "b"}])
    snapshots.save(DB(), first)
    snapshots.invalidate(DB())
    assert snapshots.current(DB()) is None
    snapshots.save(DB(), second)
    assert snapshots.current(DB()).version == "v2"
    assert snapshots.by_version(DB(), "v1").items == [{"id": "a"}]
    snapshots._memory.clear()


def test_card_badges_disclose_paid_outside_paid_slots(monkeypatch):
    from feed import badges

    monkeypatch.setattr(badges, "paid_placements", lambda c, ids: {"b": "boosted", "s": "sponsored"})
    monkeypatch.setattr(badges, "paid_open", lambda c: True)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = [
        {"id": "b", "item_type": "product", "created_at": old},
        {"id": "s", "item_type": "service", "created_at": old},
        {"id": "t", "item_type": "product", "created_at": old},
        {"id": "o", "item_type": "product", "created_at": old},
    ]
    out = badges.card_statuses(None, rows, placements={"o": "organic"}, trending={"t", "b"})
    assert out == {"b": "boosted", "s": "sponsored", "t": "trending", "o": None}
