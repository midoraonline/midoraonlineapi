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
