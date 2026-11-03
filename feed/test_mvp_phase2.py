from datetime import datetime, timedelta, timezone

import pytest

from feed import config as C
from feed import meaningful, scoring
from feed.quality import listing_quality

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def _ago(hours, **extra):
    return {"created_at": (NOW - timedelta(hours=hours)).isoformat(), **extra}


def test_weights_follow_mvp_sub_stages():
    assert scoring.weights(10) == (40, 45, 15)
    assert scoring.weights(80) == (45, 35, 20)
    assert scoring.weights(150) == (50, 30, 20)


@pytest.mark.parametrize(
    ("quality", "fresh_hours", "impressions", "expected"),
    [(82, 1, 0, 91.9), (96, 150, 0, 78.95), (60, 1, 0, 82.0), (95, 30, 15, 73.0)],
)
def test_worked_example_from_the_strategy(quality, fresh_hours, impressions, expected):
    parts = scoring.components(
        _ago(fresh_hours), quality=quality, shop_meta=None, impressions=impressions, eligible=80, stage=2, now=NOW
    )
    assert round(sum(parts.values()), 2) == expected


def test_exposure_bands():
    assert [scoring.exposure(n) for n in (0, 2, 5, 10, 20, 21)] == [100, 80, 60, 40, 20, 5]


def test_services_age_slower_and_closing_opportunities_stay_visible():
    assert scoring.freshness(_ago(40, item_type="product"), NOW) == 75
    assert scoring.freshness(_ago(40, item_type="service"), NOW) == 90
    closing = _ago(800, item_type="opportunity", listing_meta={"deadline": "2026-11-05"})
    assert scoring.freshness(closing, NOW) == scoring.CLOSING_OPPORTUNITY_FRESHNESS


def test_quality_uses_a_rubric_per_type():
    opp = {
        "item_type": "opportunity", "title": "Sales assistant wanted", "description": "z" * 90,
        "location_name": "Kampala",
        "listing_meta": {"opportunity_kind": "job", "deadline": "2026-12-01", "requirements": "Diploma, 2 years"},
    }
    assert listing_quality(opp) == 100
    assert listing_quality({**opp, "listing_meta": {}}) == 60


def test_premium_and_free_listings_score_the_same():
    row = _ago(10, item_type="product")
    args = dict(quality=80, fraud="low", impressions=3, eligible=60, stage=2, now=NOW)
    premium = {"trust_score": 5, "seller_score": 100, "trust_badges": ["business_verified"], "plan": "premium"}
    assert scoring.score(row, shop_meta=premium, **args) == scoring.score(row, shop_meta=None, **args)


def test_meaningful_updates():
    before = {"price_ugx": 100000, "image_urls": ["a"], "description": "short", "status": "active",
              "listing_meta": {"deadline": "2026-11-10"}, "stock_quantity": 0}
    assert meaningful.is_meaningful(before, {"price_ugx": 85000})
    assert not meaningful.is_meaningful(before, {"price_ugx": 98000})
    assert meaningful.is_meaningful(before, {"image_urls": ["a", "b"]})
    assert not meaningful.is_meaningful(before, {"image_urls": ["a"]})
    assert not meaningful.is_meaningful(before, {"description": "short."})
    assert meaningful.is_meaningful(before, {"description": "x" * 120})
    assert meaningful.is_meaningful(before, {"listing_meta": {"deadline": "2026-11-20"}})
    assert meaningful.is_meaningful(before, {"stock_quantity": 4})
    assert meaningful.is_meaningful({**before, "status": "sold"}, {"status": "active"})
    assert not meaningful.is_meaningful(before, {"title": "Fixed a typo"})


def test_freshness_refresh_respects_cooldown():
    assert not meaningful.refresh_allowed((NOW - timedelta(hours=10)).isoformat(), NOW)
    assert meaningful.refresh_allowed((NOW - timedelta(hours=C.FRESHNESS_RESET_COOLDOWN_HOURS)).isoformat(), NOW)
