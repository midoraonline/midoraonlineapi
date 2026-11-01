"""Admin audit history and the last-admin demotion guard."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from admin import audit
from admin.routes import users as users_routes


class _Query:
    def __init__(self, store: dict, table: str):
        self.store, self.table, self.filters, self.payload, self.op = store, table, [], None, "select"

    def select(self, *_a, **_k):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) == str(val))
        return self

    def neq(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) != str(val))
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: str(r.get(col)) in {str(v) for v in vals})
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(self.payload)
            return SimpleNamespace(data=[self.payload], count=None)
        hits = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
        return SimpleNamespace(data=[dict(r) for r in hits], count=len(hits))


class _Admin:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name):
        return _Query(self.store, name)


def _admin_user(uid: str) -> dict:
    return {"id": uid, "user_role": "admin", "status": "active", "full_name": f"Admin {uid}"}


@pytest.fixture
def store(monkeypatch):
    data: dict = {}
    monkeypatch.setattr(users_routes, "get_supabase_admin", lambda: _Admin(data))
    yield data
    data.clear()


def test_record_admin_action_writes_row():
    # Arrange
    data: dict = {}
    # Act
    audit.record_admin_action(
        _Admin(data), actor="a1", action="plan.updated", target_type="plan", target_id="pro",
        before={"price_ugx": 1}, after={"price_ugx": 2},
    )
    # Assert
    row = data["admin_audit_log"][0]
    assert (row["actor_id"], row["target_id"], row["after"]) == ("a1", "pro", {"price_ugx": 2})


def test_record_admin_action_never_raises_when_table_missing():
    # Arrange
    broken = SimpleNamespace(table=lambda _n: (_ for _ in ()).throw(RuntimeError("no table")))
    # Act / Assert
    audit.record_admin_action(broken, actor=None, action="x", target_type="user", target_id="u")


def test_list_admin_actions_adds_actor_names():
    # Arrange
    data = {
        "admin_audit_log": [{"actor_id": "a1", "target_type": "user", "target_id": "u1"}],
        "users": [_admin_user("a1")],
    }
    # Act
    result = audit.list_admin_actions(_Admin(data), target_type="user")
    # Assert
    assert result["items"][0]["actor_name"] == "Admin a1"


def test_role_change_is_audited(store):
    # Arrange
    store["users"] = [_admin_user("a1"), {"id": "u1", "user_role": "customer", "status": "active"}]
    # Act
    asyncio.run(users_routes.admin_change_role("u1", users_routes.RoleBody(role="merchant"), SimpleNamespace(sub="a1")))
    # Assert
    row = store["admin_audit_log"][0]
    assert row["before"] == {"role": "customer"} and row["after"] == {"role": "merchant"}


def test_last_admin_cannot_be_demoted(store):
    # Arrange
    store["users"] = [{**_admin_user("a1"), "status": "deleted"}, _admin_user("a2")]
    # Act
    with pytest.raises(HTTPException) as exc:
        asyncio.run(users_routes.admin_change_role("a2", users_routes.RoleBody(role="customer"), SimpleNamespace(sub="a1")))
    # Assert
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "last_admin"


def test_admin_can_be_demoted_when_another_admin_exists(store):
    # Arrange
    store["users"] = [_admin_user("a1"), _admin_user("a2")]
    # Act
    result = asyncio.run(
        users_routes.admin_change_role("a2", users_routes.RoleBody(role="merchant"), SimpleNamespace(sub="a1"))
    )
    # Assert
    assert result["user_role"] == "merchant"
