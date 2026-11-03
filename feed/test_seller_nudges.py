from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from feed import seller_nudges as N

NOW = datetime(2026, 11, 3, 4, tzinfo=timezone.utc)


def test_nudge_thresholds():
    assert N.stock_nudge_reason(stock=2, views=8, contacts=0) == "views"
    assert N.stock_nudge_reason(stock=2, views=7, contacts=5) is None
    assert N.stock_nudge_reason(stock=2, views=0, contacts=6) == "contacts"
    assert N.stock_nudge_reason(stock=0, views=99, contacts=99) is None


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


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(N, "_send", lambda user, title, body, pid, kind, extra=None: sent.append((user, pid, kind, body)))
    return sent


def test_stock_nudges_go_to_the_owner_once_per_cooldown(monkeypatch):
    sent = _capture(monkeypatch)
    db = _DB({
        "products": [
            {"id": "busy", "shop_id": "s1", "title": "Phone", "item_type": "product", "stock_quantity": 2},
            {"id": "quiet", "shop_id": "s1", "title": "Case", "item_type": "product", "stock_quantity": 5},
            {"id": "asked", "shop_id": "s1", "title": "Charger", "item_type": "product", "stock_quantity": 1},
        ],
        "shops": [{"id": "s1", "owner_id": "seller"}],
        "notifications": [
            {"metadata": {"type": N.STOCK_NUDGE, "product_id": "asked"}, "created_at": (NOW - timedelta(days=2)).isoformat()},
        ],
    })
    monkeypatch.setattr(
        "shop.listing_stats.by_listing",
        lambda db, ids, since: {
            "busy": {"views": 9, "whatsapp_clicks": 0, "messages": 0},
            "quiet": {"views": 10, "whatsapp_clicks": 1, "messages": 1},
            "asked": {"views": 50, "whatsapp_clicks": 9},
        },
    )
    assert N.run_stock_nudges(db, NOW) == 1
    assert sent[0][:3] == ("seller", "busy", N.STOCK_NUDGE) and "2 left" in sent[0][3]


def test_contacts_trigger_a_nudge(monkeypatch):
    sent = _capture(monkeypatch)
    db = _DB({
        "products": [{"id": "p", "shop_id": "s1", "title": "Shoes", "item_type": "product", "stock_quantity": 1}],
        "shops": [{"id": "s1", "owner_id": "seller"}],
    })
    monkeypatch.setattr("shop.listing_stats.by_listing", lambda db, ids, since: {"p": {"views": 1, "whatsapp_clicks": 2, "messages": 1}})
    assert N.run_stock_nudges(db, NOW) == 1
    assert "3 buyers contacted you" in sent[0][3]


def test_expired_listing_tells_the_seller(monkeypatch):
    sent = _capture(monkeypatch)
    db = _DB({"shops": [{"id": "s1", "owner_id": "seller"}]})
    rows = [{"id": "j1", "shop_id": "s1", "title": "Cashier", "item_type": "opportunity"},
            {"id": "v1", "shop_id": "s1", "title": "Cleaning", "item_type": "service"}]
    assert N.notify_expired(db, rows) == 2
    assert [s[2] for s in sent] == [N.EXPIRED, N.EXPIRED]
    assert "new deadline" in sent[0][3] and "taking clients" in sent[1][3]


def test_past_deadline_blocks_reactivation():
    import pytest
    from fastapi import HTTPException

    from shop.routes.products import _reject_past_deadline

    with pytest.raises(HTTPException) as err:
        _reject_past_deadline({"item_type": "service", "listing_meta": {"deadline": "2020-01-01"}})
    assert err.value.detail["code"] == "deadline_passed"
    _reject_past_deadline({"item_type": "service", "listing_meta": {"deadline": "2999-01-01"}})
