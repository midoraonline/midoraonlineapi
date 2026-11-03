from feed import config as C
from feed import placement as P
from feed.stages import StagePlan

PLAN = StagePlan(stage=1, eligible=40)


def _e(pid, shop, score, lane="product", cat="", quality=80.0):
    return {"id": pid, "shop": shop, "lane": lane, "cat": cat, "score": score, "quality": quality,
            "impressions": 0, "fresh": 50.0}


def _max_run(values):
    best = run = 0
    prev = object()
    for v in values:
        run = run + 1 if v == prev else 1
        prev = v
        best = max(best, run)
    return best


def test_fewer_than_a_block_returns_only_unique_cards():
    out = P.place([_e(f"p{i}", f"s{i}", 10 - i) for i in range(7)], PLAN)
    assert [e["id"] for e in out] == [f"p{i}" for i in range(7)]


def test_no_long_type_runs_while_other_types_remain():
    entries = [_e(f"p{i}", f"ps{i}", 100 - i) for i in range(12)]
    entries += [_e(f"s{i}", f"ss{i}", 50 - i, lane="service") for i in range(3)]
    out = P.place(entries, PLAN)
    lanes = [e["lane"] for e in out]
    assert _max_run(lanes) <= C.MAX_SAME_TYPE_RUN
    assert lanes[: C.MAX_SAME_TYPE_RUN + 1].count("service") == 1


def test_no_long_subcategory_runs_while_alternatives_remain():
    entries = [_e(f"ph{i}", f"a{i}", 100 - i, cat="phones") for i in range(6)]
    entries += [_e(f"lp{i}", f"b{i}", 50 - i, cat="laptops") for i in range(3)]
    cats = [e["cat"] for e in P.place(entries, PLAN)]
    assert cats[:4].count("laptops") == 1
    assert _max_run(cats[:7]) <= C.MAX_SAME_SUBCATEGORY_RUN


def test_seller_cap_relaxes_step_by_step_instead_of_ending_the_feed():
    entries = [_e(f"big{i}", "big", 100 - i) for i in range(10)] + [_e(f"o{i}", f"s{i}", 10 - i) for i in range(5)]
    out = P.place(entries, PLAN)
    assert len(out) == 15
    reasons = [e["reason"] for e in out]
    assert "seller_cap_3" in reasons and "seller_cap_4" in reasons and reasons.count("ranked") >= 7
    assert reasons.index("seller_cap_3") < reasons.index("seller_cap_4") < reasons.index("seller_cap_open")


def test_first_block_keeps_a_quality_floor_when_supply_permits():
    entries = [_e("weak_new", "w", 999, quality=50)] + [_e(f"g{i}", f"s{i}", 50 - i) for i in range(25)]
    ids = [e["id"] for e in P.place(entries, PLAN)]
    assert ids.index("weak_new") >= C.BLOCK_SIZE


def test_quality_floor_relaxes_when_supply_is_thin():
    entries = [_e("weak", "w", 99, quality=50), _e("ok", "o", 10, quality=50)]
    out = P.place(entries, PLAN)
    assert [e["id"] for e in out] == ["weak", "ok"]
    assert out[0]["reason"] == "ranked"


def test_every_card_records_why_it_was_placed():
    out = P.place([_e(f"p{i}", "same", 10 - i) for i in range(3)], PLAN)
    assert [e["reason"] for e in out] == ["ranked", "seller_gap_relaxed", "seller_cap_3"]
    assert all(e["mode"] == "organic" for e in out)
