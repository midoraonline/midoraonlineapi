import asyncio

import pytest

from auth import account_deletion as ad


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def is_(self, *_):
        return self

    def limit(self, *_):
        return self

    def range(self, *_):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _match(self, row):
        for kind, col, val in self.filters:
            if kind == "eq" and str(row.get(col)) != str(val):
                return False
            if kind == "neq" and str(row.get(col)) == str(val):
                return False
        return True

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        hit = [r for r in rows if self._match(r)]
        if self.op == "delete":
            if self.table in self.db.get("_fk_block", ()):
                raise RuntimeError("violates foreign key constraint")
            self.db[self.table] = [r for r in rows if r not in hit]
        elif self.op == "update":
            for r in hit:
                r.update(self.payload)

        class R:
            data = hit
            count = len(hit)

        return R()


class _Auth:
    class admin:
        calls: list = []

        @staticmethod
        def delete_user(uid):
            raise RuntimeError("User not found")


class _Client:
    auth = _Auth

    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(self.db, name)


def _db(**extra):
    db = {
        "users": [
            {"id": "u1", "email": "a@x.ug", "user_role": "merchant", "status": "active"},
            {"id": "admin", "email": "b@x.ug", "user_role": "admin", "status": "active"},
        ],
        "shops": [{"id": "s1", "owner_id": "u1", "logo_url": None, "theme_config": None}],
        "products": [{"id": "p1", "shop_id": "s1", "image_urls": [], "image_keys": ["k1"], "video_keys": []}],
        "profiles": [{"id": "u1", "avatar_url": None}],
        "media_uploads": [{"key": "k2", "user_id": "u1"}],
        "refresh_tokens": [{"user_id": "u1", "revoked_at": None}],
    }
    db.update(extra)
    return db


def test_hard_delete_removes_everything(monkeypatch):
    monkeypatch.setattr("media.references.load_referenced_keys", lambda _c: set())
    deleted: list[str] = []

    async def fake_delete(keys):
        deleted.extend(keys)

    db = _db()
    summary = asyncio.run(ad.delete_account("u1", client=_Client(db), delete_files=fake_delete))
    assert summary["user"] == "deleted"
    assert summary["shops_deleted"] == 1
    assert summary["supabase_auth"] == "none"
    assert [u["id"] for u in db["users"]] == ["admin"]
    assert db["products"] == [] and db["shops"] == []
    assert sorted(deleted) == ["k1", "k2"]


def test_fk_blocked_user_is_anonymized(monkeypatch):
    monkeypatch.setattr("media.references.load_referenced_keys", lambda _c: set())
    db = _db(_fk_block=("users", "shops"))

    async def noop(_k):
        return None

    summary = asyncio.run(ad.delete_account("u1", client=_Client(db), delete_files=noop))
    user = next(u for u in db["users"] if u["id"] == "u1")
    assert summary["user"] == "anonymized"
    assert user["status"] == "deleted" and user["email"].startswith("deleted-u1@")
    assert user["full_name"] == "Deleted user"
    assert db["shops"][0]["is_active"] is False and db["shops"][0]["slug"] == "deleted-s1"


def test_last_admin_cannot_be_deleted():
    db = _db()
    with pytest.raises(ad.DeletionError) as e:
        asyncio.run(ad.delete_account("admin", client=_Client(db)))
    assert e.value.code == "last_admin"
