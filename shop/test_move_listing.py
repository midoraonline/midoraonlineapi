"""Moving a listing between shops / to Other, and the explicit no-shop post."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shop import move
from shop.personal import resolve_shop_for_direct_post


@pytest.mark.parametrize(
    ("shop_type", "item_type", "ok"),
    [
        ("product", "product", True),
        ("service", "product", False),
        ("both", "product", True),
        ("product", "service", False),
        ("service", "service", True),
        ("both", "service", True),
        ("product", "opportunity", True),
        ("service", "opportunity", True),
        ("service", "job", True),
        (None, "product", True),
    ],
)
def test_shop_accepts(shop_type, item_type, ok):
    assert move.shop_accepts(shop_type, item_type) is ok


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def limit(self, _n):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = [r for r in self.db[self.table] if all(r.get(k) == v for k, v in self.f)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return SimpleNamespace(data=rows, count=len(rows))


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables, name)


def _db(item_type="product", shop_type="product"):
    return _DB(
        {
            "products": [{"id": "p1", "shop_id": "s0", "item_type": item_type}],
            "shops": [
                {"id": "s0", "owner_id": "u1", "shop_type": "both", "is_personal": False},
                {"id": "s1", "owner_id": "u1", "shop_type": shop_type, "is_personal": False},
                {"id": "s2", "owner_id": "other", "shop_type": "both", "is_personal": False},
            ],
        }
    )


def _room(ok=True):
    def fn(*_a):
        if not ok:
            raise PermissionError("full")

    return patch.object(move.plan_service, "assert_shop_has_room", fn)


def test_move_to_own_shop_updates_shop_id():
    db = _db()
    with _room():
        assert move.move_listing(db, "p1", "u1", "s1") == "s1"
    assert db.tables["products"][0]["shop_id"] == "s1"


def test_cannot_move_to_someone_elses_shop():
    db = _db()
    with _room(), pytest.raises(PermissionError):
        move.move_listing(db, "p1", "u1", "s2")
    assert db.tables["products"][0]["shop_id"] == "s0"


def test_cannot_move_someone_elses_listing():
    with _room(), pytest.raises(PermissionError):
        move.move_listing(_db(), "p1", "intruder", "s1")


def test_wrong_shop_type_is_refused():
    db = _db(item_type="service", shop_type="product")
    with _room(), pytest.raises(move.MoveError) as err:
        move.move_listing(db, "p1", "u1", "s1")
    assert err.value.code == "shop_type_mismatch"
    assert db.tables["products"][0]["shop_id"] == "s0"


def test_plan_cap_is_refused():
    db = _db()
    with _room(False), pytest.raises(move.MoveError) as err:
        move.move_listing(db, "p1", "u1", "s1")
    assert err.value.code == "plan_limit"


def test_other_moves_to_personal_profile():
    db = _db()
    with _room(), patch.object(move, "get_or_create_personal_shop", lambda *_a: ("personal", False)):
        assert move.move_listing(db, "p1", "u1", None) == "personal"
    assert db.tables["products"][0]["shop_id"] == "personal"


def test_moving_to_same_shop_is_a_no_op():
    db = _db()
    with _room(False):
        assert move.move_listing(db, "p1", "u1", "s0") == "s0"


def test_personal_only_ignores_a_single_real_shop():
    with patch("shop.personal.get_or_create_personal_shop", lambda *_a: ("personal", False)):
        assert resolve_shop_for_direct_post(object(), "u1", personal_only=True) == ("personal", False)
