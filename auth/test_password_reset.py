from datetime import datetime, timedelta, timezone

import pytest

from auth import password_reset as pr


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.updates = db, table, None

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def ilike(self, *_):
        return self

    def is_(self, *_):
        return self

    def limit(self, *_):
        return self

    def insert(self, row):
        self.db.setdefault("inserts", []).append((self.table, row))
        return self

    def update(self, row):
        self.db.setdefault("updates", []).append((self.table, row))
        return self

    def execute(self):
        class R:
            pass

        r = R()
        r.data = self.db.get(self.table, [])
        return r


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(self.db, name)


def _run(monkeypatch, row):
    db = {"password_reset_tokens": [row] if row else []}
    monkeypatch.setattr(pr, "get_supabase_admin", lambda: _Client(db))
    monkeypatch.setattr("auth.service.hash_password", lambda p: "h:" + p)
    return db


def test_valid_token_updates_password_and_revokes(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    db = _run(monkeypatch, {"id": "t", "user_id": "u", "expires_at": future, "used_at": None})
    assert pr.reset_password("tok", "newpassword") == "u"
    tables = [t for t, _ in db["updates"]]
    assert ("users", {"password_hash": "h:newpassword"}) in db["updates"]
    assert "refresh_tokens" in tables


def test_expired_and_used(monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _run(monkeypatch, {"id": "t", "user_id": "u", "expires_at": past, "used_at": None})
    with pytest.raises(pr.ResetError) as e:
        pr.reset_password("tok", "newpassword")
    assert e.value.code == "reset_expired"
    _run(monkeypatch, {"id": "t", "user_id": "u", "expires_at": past, "used_at": past})
    with pytest.raises(pr.ResetError) as e:
        pr.reset_password("tok", "newpassword")
    assert e.value.code == "reset_used"
    _run(monkeypatch, None)
    with pytest.raises(pr.ResetError):
        pr.reset_password("tok", "newpassword")


def test_unknown_email_returns_none(monkeypatch):
    monkeypatch.setattr(pr, "get_supabase_admin", lambda: _Client({}))
    assert pr.create_reset_token("nobody@x.ug") is None
