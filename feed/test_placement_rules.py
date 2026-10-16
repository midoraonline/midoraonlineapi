from feed import config as C
from feed.placement import bucketize, place


def _e(pid, shop, score):
    return {"product": {"id": pid, "shop_id": shop}, "score": score}


def _pools(organic):
    return {k: [] for k in ("super_boost", "boosted", "premium_store", "fresh", "exploration")} | {"organic": organic}


def test_no_sponsored_pool_or_cadence():
    pools = bucketize([], boost_map={}, shop_meta={}, signals={"categories": set()}, now=None)
    assert "sponsored" not in pools
    assert all(s.kind != "sponsored" for s in C.COMPOSITION_CADENCE)


def test_vendor_penalty_changes_choice():
    # Shop A already placed once; its 2nd item (101) should lose to B's 100
    # because the penalty for A's 2nd slot (K=5) drops it to 96.
    organic = [_e("a1", "A", 200), _e("a2", "A", 101), _e("b1", "B", 100)]
    out = place(_pools(organic), limit=3)
    assert [x["product"]["id"] for x in out] == ["a1", "b1", "a2"]
    assert out[1]["adjusted_score"] == 100


def test_penalty_keeps_order_when_gap_is_large():
    organic = [_e("a1", "A", 200), _e("a2", "A", 190), _e("b1", "B", 100)]
    out = place(_pools(organic), limit=3)
    assert [x["product"]["id"] for x in out] == ["a1", "a2", "b1"]
