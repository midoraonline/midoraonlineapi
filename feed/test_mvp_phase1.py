from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from feed import badges, expiry, scoring
from feed import config as C
from feed.stages import plan_for

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def _opp(pid, deadline, item_type="opportunity"):
    return {"id": pid, "item_type": item_type, "listing_meta": {"deadline": deadline}}


def test_paid_placement_is_off_until_200_listings():
    assert not plan_for({"product": 150, "service": 40, "opportunity": 9}).paid
    assert plan_for({"product": 150, "service": 40, "opportunity": 10}).paid


def test_expired_opportunities_are_never_eligible():
    rows = [
        _opp("past", "2026-11-02"),
        _opp("today", "2026-11-03"),
        _opp("job_past", "2026-10-01", item_type="job"),
        _opp("no_deadline", ""),
        {"id": "product", "item_type": "product", "listing_meta": {"deadline": "2020-01-01"}},
    ]
    assert [r["id"] for r in expiry.drop_expired(rows, NOW)] == ["today", "no_deadline", "product"]


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.updated: list[tuple[dict, list[str]]] = []

    def table(self, _name):
        return _Q(self)


class _Q:
    def __init__(self, db):
        self.db = db
        self.patch = None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def in_(self, key, values):
        if self.patch is not None and key == "id":
            self.db.updated.append((self.patch, list(values)))
        return self

    def limit(self, _n):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        return SimpleNamespace(data=self.db.rows if self.patch is None else [])


def test_close_expired_closes_only_past_deadlines(monkeypatch):
    from feed import seller_nudges

    told = []
    monkeypatch.setattr(seller_nudges, "notify_expired", lambda db, rows: told.extend(r["id"] for r in rows))
    db = _DB([_opp("past", "2026-11-01"), _opp("open", "2026-12-01"), _opp("svc", "2026-10-30", item_type="service")])
    assert expiry.close_expired(db, NOW) == ["past", "svc"]
    assert db.updated == [({"status": "expired", "is_published": False, "stock_quantity": 0}, ["past", "svc"])]
    assert told == ["past", "svc"]


def test_trust_does_not_change_home_score():
    args = dict(quality=80.0, fraud="low", impressions=0, eligible=10, stage=1, now=NOW)
    verified = {"trust_score": 5, "seller_score": 100, "trust_badges": ["business_verified"]}
    assert C.TRUST_WEIGHT == 0
    assert scoring.score({}, shop_meta=verified, **args) == scoring.score({}, shop_meta=None, **args)


def test_no_paid_badges_while_paid_stage_is_closed(monkeypatch):
    monkeypatch.setattr(badges, "paid_placements", lambda c, ids: {"b": "boosted"})
    monkeypatch.setattr(badges, "paid_open", lambda c: False)
    old = (NOW - timedelta(days=30)).isoformat()
    out = badges.card_statuses(None, [{"id": "b", "item_type": "product", "created_at": old}], trending=set())
    assert out == {"b": None}


def test_boosts_cannot_be_bought_before_paid_stage(monkeypatch):
    from shop import listing_actions as LA

    monkeypatch.setattr(LA, "owned_listing", lambda *a: {"id": "l1", "status": "active", "is_published": True})
    monkeypatch.setattr(LA, "boosts_open", lambda admin: False)
    with pytest.raises(HTTPException) as err:
        LA.boost_listing(None, "l1", "u1", "pkg", True)
    assert err.value.status_code == 409 and err.value.detail["code"] == "boosts_closed"
