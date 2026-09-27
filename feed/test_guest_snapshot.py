"""Guest home page reads a shared ranking, then hydrates one page."""

from types import SimpleNamespace

from feed import snapshots
from feed.service import _get_latest_feed_page


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self._select = "*"
        self._filters = []
        self._limit = None
        self._op = "select"
        self._payload = None

    def select(self, cols, *_args, **_kwargs):
        self._select = cols
        self.client.log.append(("select", self.table, cols))
        return self

    def eq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def in_(self, key, values):
        wanted = {str(v) for v in values}
        self._filters.append(lambda row, key=key, wanted=wanted: str(row.get(key)) in wanted)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def upsert(self, payload, on_conflict=None):
        self._op = "upsert"
        self._payload = payload
        self.client.log.append(("upsert", self.table))
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        if self._op == "upsert":
            rows = [
                row for row in self.client.tables[self.table]
                if row.get("snapshot_key") != self._payload.get("snapshot_key")
            ]
            rows.append(self._payload)
            self.client.tables[self.table] = rows
            return SimpleNamespace(data=[self._payload])
        if self._op == "delete":
            self.client.tables[self.table] = []
            return SimpleNamespace(data=[])
        rows = []
        for row in self.client.tables.get(self.table, []):
            if all(check(row) for check in self._filters):
                rows.append(row)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _Client:
    def __init__(self, products):
        self.tables = {
            "products": products,
            "product_reviews": [
                {"product_id": "p1", "rating": 5},
                {"product_id": "p1", "rating": 3},
            ],
            "feed_rank_snapshots": [],
        }
        self.log = []

    def table(self, name):
        self.tables.setdefault(name, [])
        return _Query(self, name)

    def rpc(self, *_args, **_kwargs):
        raise RuntimeError("rpc missing")


def _products():
    return [
        {
            "id": pid,
            "shop_id": "s1",
            "title": pid,
            "description": "A long enough line.",
            "price_ugx": 1000,
            "stock_quantity": 1,
            "image_urls": [],
            "category": "Phones",
            "item_type": "product",
            "is_published": True,
            "status": "active",
            "created_at": stamp,
            "listing_score": 1,
            "view_count": 0,
            "is_negotiable": True,
        }
        for pid, stamp in (
            ("p1", "2026-01-03T00:00:00+00:00"),
            ("p2", "2026-01-02T00:00:00+00:00"),
            ("p3", "2026-01-01T00:00:00+00:00"),
        )
    ]


def test_guest_page_hydrates_from_snapshot_on_the_second_read():
    snapshots.clear_memory()
    client = _Client(_products())
    first, has_more = _get_latest_feed_page(client, page=1, limit=2)
    assert [card.id for card in first] == ["p1", "p2"]
    assert has_more is True
    assert first[0].average_rating == 4.0
    assert first[0].review_count == 2
    lean = [entry[2] for entry in client.log if entry[0] == "select" and entry[1] == "products"]
    assert lean[0] == "id,shop_id,created_at"
    assert any(item[0] == "upsert" for item in client.log)

    client.log.clear()
    snapshots.clear_memory()
    second, _ = _get_latest_feed_page(client, page=1, limit=2)
    assert [card.id for card in second] == ["p1", "p2"]
    product_selects = [entry[2] for entry in client.log if entry[0] == "select" and entry[1] == "products"]
    assert product_selects
    assert all(cols != "id,shop_id,created_at" for cols in product_selects)
    assert not any(item[0] == "upsert" for item in client.log)
