import pytest
from fastapi import HTTPException

from admin import boost_grants as svc


class Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: str(r.get(k)) == str(v))
        return self

    def in_(self, k, vs):
        vs = {str(v) for v in vs}
        self.filters.append(lambda r: str(r.get(k)) in vs)
        return self

    def gte(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            new = [{"id": f"b{len(rows) + i}", **r} for i, r in enumerate(new)]
            rows.extend(new)
            return type("R", (), {"data": new})
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return type("R", (), {"data": hit})


class DB:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        return Q(self.data, name)


@pytest.fixture(autouse=True)
def no_refresh(monkeypatch):
    monkeypatch.setattr(svc, "_refresh", lambda ids: None)


def make_db():
    return DB({
        "boost_plans": [{"id": "p1", "duration_hours": 168, "score_bonus": 50}],
        "shops": [{"id": "s1", "name": "Shop", "owner_id": "u1"}],
        "products": [
            {"id": "l1", "shop_id": "s1", "status": "active", "is_published": True},
            {"id": "l2", "shop_id": "s1", "status": "active", "is_published": True},
            {"id": "l3", "shop_id": "s1", "status": "draft", "is_published": False},
        ],
        "listing_boosts": [{"id": "old", "listing_id": "l1", "active": True, "source": "paid"}],
    })


def test_resolve_terms_package_and_override():
    db = make_db()
    assert svc.resolve_terms(db, "p1", None, None) == {"boost_plan_id": "p1", "hours": 168, "score_bonus": 50}
    assert svc.resolve_terms(db, "p1", 2, 300)["hours"] == 2
    assert svc.resolve_terms(db, None, 5, 10)["boost_plan_id"] is None


@pytest.mark.parametrize("args", [(None, 5, None), (None, None, None), ("nope", None, None), (None, 0, 10), (None, 5, 999)])
def test_resolve_terms_rejects(args):
    with pytest.raises(HTTPException):
        svc.resolve_terms(make_db(), *args)


def test_boost_listing_admin_source_replaces_existing():
    db = make_db()
    out = svc.boost_listing(db, "l1", {"boost_plan_id": None, "hours": 3, "score_bonus": 100}, "admin1")
    b = out["boost"]
    assert b["source"] == "admin" and b["granted_by"] == "admin1" and b["seller_id"] == "u1"
    assert b["payment_status"] == "completed" and b["active"] is True
    old = next(r for r in db.data["listing_boosts"] if r["id"] == "old")
    assert old["active"] is False


def test_boost_shop_covers_live_listings_and_ends():
    db = make_db()
    out = svc.boost_shop(db, "s1", {"boost_plan_id": "p1", "hours": 1, "score_bonus": 50}, "admin1")
    assert out["boosted"] == 2
    tagged = [r for r in db.data["listing_boosts"] if r.get("shop_id") == "s1"]
    assert {r["listing_id"] for r in tagged} == {"l1", "l2"}
    assert svc.end_shop_boosts(db, "s1")["ended"] == 2
    assert not any(r["active"] for r in tagged)


def test_boost_shop_without_listings():
    db = make_db()
    db.data["products"] = []
    with pytest.raises(HTTPException):
        svc.boost_shop(db, "s1", {"boost_plan_id": None, "hours": 1, "score_bonus": 1}, None)
