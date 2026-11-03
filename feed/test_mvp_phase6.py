from types import SimpleNamespace

from feed import demand


def test_request_matches_by_category_or_words_and_area():
    product = {"title": "Emergency plumber", "category": "Plumbing", "item_type": "service",
               "location_name": "Gulu", "listing_meta": {"service_areas": ["Wakiso"]}}
    assert demand.matches({"need": "plumber", "category": "Plumbing", "area": "wakiso"}, product)
    assert demand.matches({"need": "need a plumber"}, product)
    assert not demand.matches({"need": "plumber", "area": "entebbe"}, product)
    assert not demand.matches({"need": "laptop", "category": "Laptops"}, product)


def test_summary_aggregates_without_buyer_identity():
    reqs = [{"actor_id": "u1", "properties": {"need": "Plumber", "category": "Plumbing", "area": "masaka"}},
            {"actor_id": "u2", "properties": {"need": "plumber", "category": "Plumbing", "area": "masaka"}}]
    zero = [{"properties": {"query": "plumber", "location": "Masaka"}}]
    rows = demand.summary(reqs, zero)
    assert rows[0] == {"kind": "request", "what": "plumbing", "where": "masaka", "count": 2}
    assert {"kind": "zero_results", "what": "plumber", "where": "masaka", "count": 1} in rows
    assert all("actor_id" not in r for r in rows)


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_alerts_go_to_opted_in_buyers_once_and_never_the_seller(monkeypatch):
    sent = []
    monkeypatch.setattr("notifications.service.create_notification", lambda **kw: sent.append(kw["user_id"]))
    db = _DB({
        "products": [{"id": "p1", "title": "Plumber in Kira", "category": "Plumbing", "item_type": "service",
                      "location_name": "Kira", "listing_meta": {}, "shop_id": "s1"}],
        "shops": [{"owner_id": "seller"}],
        "analytics_events": [
            {"actor_id": "b1", "properties": {"need": "plumber", "notify": True}},
            {"actor_id": "b1", "properties": {"need": "plumbing", "notify": True}},
            {"actor_id": "b2", "properties": {"need": "plumber", "notify": False}},
            {"actor_id": "seller", "properties": {"need": "plumber", "notify": True}},
        ],
    })
    assert demand.notify_matches("p1", client=db) == 1
    assert sent == ["b1"]


def test_demand_endpoint_records_request(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from feed import router as feed_router

    calls = []
    monkeypatch.setattr(demand, "record_request", lambda **kw: calls.append(kw))
    app = FastAPI()
    app.include_router(feed_router.router)
    app.dependency_overrides[feed_router.get_optional_user_id] = lambda: None
    client = TestClient(app)
    r = client.post("/feed/demand", json={"need": "plumber", "area": "Kira", "notify": True})
    assert r.status_code == 202 and r.json() == {"ok": True, "alerts": False}
    assert calls[0]["area"] == "wakiso" and calls[0]["actor_id"] is None
