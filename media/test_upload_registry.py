"""media_uploads registry: ownership-checked discard, orphan sweep, banner refs."""
import asyncio

import pytest

from media import registry
from media.references import referenced_key_set, shop_banner_url
from media.registry import discardable, requested_keys


def test_banner_url_from_theme_config():
    assert shop_banner_url({"metadata": {"banner_url": " https://utfs.io/f/b "}}) == "https://utfs.io/f/b"
    assert shop_banner_url({"metadata": {}}) is None
    assert shop_banner_url(None) is None
    assert shop_banner_url({"metadata": "x"}) is None


def test_banner_is_referenced_like_a_logo():
    keys = referenced_key_set([], ["https://utfs.io/f/bannerKey"], [])
    assert "bannerKey" in keys


def test_requested_keys_dedupes_and_parses_urls():
    keys = requested_keys(["a", "a", " "], ["https://x.ufs.sh/f/b", "https://cdn.example/c.jpg"])
    assert keys == ["a", "b"]
    assert len(requested_keys([str(i) for i in range(50)], [])) == registry.MAX_DISCARD


def test_discardable_requires_owner_and_unused():
    rows = [
        {"key": "mine", "user_id": "u1"},
        {"key": "theirs", "user_id": "u2"},
        {"key": "live", "user_id": "u1"},
    ]
    assert discardable(rows, "u1", {"live"}) == ["mine"]


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_):
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: str(r.get(col)) == str(val))
        return self

    def is_(self, col, _):
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: r.get(col, "") < val)
        return self

    def limit(self, *_):
        return self

    def range(self, *_):
        return self

    def delete(self):
        self.op = "delete"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            self.db[self.table] = [r for r in rows if r not in hit]
        elif self.op == "update":
            for r in hit:
                r.update(self.payload)
        return type("R", (), {"data": hit})()


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Query(self.db, name)


@pytest.fixture
def db():
    return {
        "media_uploads": [
            {"key": "mine", "user_id": "u1", "created_at": "2020-01-01", "attached_at": None},
            {"key": "theirs", "user_id": "u2", "created_at": "2020-01-01", "attached_at": None},
            {"key": "logoKey", "user_id": "u1", "created_at": "2020-01-01", "attached_at": None},
        ],
        "products": [],
        "shops": [{"logo_url": "https://utfs.io/f/logoKey", "theme_config": None}],
        "profiles": [],
    }


def test_discard_only_deletes_own_unused(db):
    deleted = []

    async def fake_delete(keys):
        deleted.extend(keys)

    out = asyncio.run(
        registry.discard_uploads(_Client(db), "u1", ["mine", "theirs", "logoKey"], delete_files=fake_delete)
    )
    assert deleted == ["mine"]
    assert out == {"deleted": 1, "skipped": 2}
    keys = {r["key"] for r in db["media_uploads"]}
    assert keys == {"theirs", "logoKey"}


def test_orphan_sweep_keeps_used_files(db):
    deleted = []

    async def fake_delete(keys):
        deleted.extend(keys)

    out = asyncio.run(registry.sweep_orphan_uploads(_Client(db), delete_files=fake_delete))
    assert sorted(deleted) == ["mine", "theirs"]
    assert out == {"orphans_deleted": 2, "attached": 1}
    assert db["media_uploads"][0]["attached_at"] is not None
