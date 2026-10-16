from datetime import datetime, timezone

from feed import boost_slots as B
from feed.placement import bucketize


def test_boosted_listing_stays_in_natural_pool():
    entry = {"product": {"id": "x", "shop_id": "s", "category": "c"}, "score": 999}
    pools = bucketize([entry], boost_map={"x": {"score_bonus": 75}}, shop_meta={},
                      signals={"categories": {"c"}}, now=datetime.now(timezone.utc))
    assert pools["organic"] == [entry]
    assert "super_boost" not in pools and "boosted" not in pools


def test_slot_math():
    assert [p for p in range(1, 25) if B.is_slot(p, 7, 3)] == [3, 10, 17, 24]
    assert B.slots_before(2, 7, 3) == 0
    assert B.slots_before(10, 7, 3) == 2


def test_compose_inserts_slots_without_duplicates_and_paginates():
    organic = [f"o{i}" for i in range(40)]
    seq = ["b1", "b2"]
    p1, more1 = B.compose_page(organic, seq, page=1, limit=10, every=7, phase=3)
    p2, more2 = B.compose_page(organic, seq, page=2, limit=10, every=7, phase=3)
    assert p1[2] == "b1" and p1[9] == "b2"
    assert len(p1) == len(set(p1)) == 10 and len(p2) == len(set(p2)) == 10
    organic_seen = [i for i in p1 + p2 if i.startswith("o")]
    assert organic_seen == organic[: len(organic_seen)]  # no skips, no repeats
    assert more1 and more2


def test_compose_skips_boost_already_on_page_organically():
    organic = ["b1"] + [f"o{i}" for i in range(20)]
    page, _ = B.compose_page(organic, ["b1", "b2"], page=1, limit=10, every=7, phase=3)
    assert page.count("b1") == 1 and page[2] == "b2"


def test_compose_without_boosts_is_plain_slice():
    organic = [str(i) for i in range(30)]
    assert B.compose_page(organic, [], page=2, limit=10) == (organic[10:20], True)


def test_rotation_weights_by_score_bonus():
    seq = B.rotation([{"id": "big", "score_bonus": 75}, {"id": "small", "score_bonus": 25}])
    assert seq.count("big") == 3 and seq.count("small") == 1


def test_relevance_gate_with_fallback():
    boosts = [{"id": "a", "category": "phones", "score_bonus": 50},
              {"id": "b", "category": "cars", "score_bonus": 50},
              {"id": "c", "category": "shoes", "score_bonus": 50}]
    got = B.relevant(boosts, categories={"phones"}, taste={"c": 0.9}, min_similarity=0.35)
    assert [b["id"] for b in got] == ["a", "c"]
    assert B.relevant(boosts, categories={"toys"}, taste={}, min_similarity=0.35) == boosts


def test_guest_gets_all_boosts(monkeypatch):
    rows = [{"id": "a", "category": "x", "score_bonus": 25, "shop_id": "s"},
            {"id": "b", "category": "y", "score_bonus": 25, "shop_id": "t"}]
    monkeypatch.setattr(B, "active_boosts", lambda c: rows)
    assert B.boosts_for_viewer(None, user_vector=None, categories=None, signed_in=False) == ["a", "b"]


def test_single_boost_uses_one_slot_per_page_and_never_skips():
    organic = [f"o{i}" for i in range(40)]
    pages = [B.compose_page(organic, ["b"], page=p, limit=10, every=7, phase=3)[0] for p in (1, 2, 3)]
    assert all(pg.count("b") == 1 and len(pg) == 10 for pg in pages)
    flat = [i for pg in pages for i in pg if i != "b"]
    assert flat == organic[:27]
