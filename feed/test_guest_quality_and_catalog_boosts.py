from feed import service
from feed.catalog import ListingFilters


def _row(pid, shop="s", title="Good title", images=("https://i/1.jpg",)):
    return {"id": pid, "shop_id": shop, "title": title, "image_urls": list(images)}


def test_guest_quality_floor_and_fraud(monkeypatch):
    monkeypatch.setattr(service.sig, "collect_shop_meta", lambda c, ids: {"s": {"is_active": True}, "dead": {"is_active": False}})
    monkeypatch.setattr(service.sig, "collect_fraud_severity", lambda c, ids, by, meta: {"bad": "high", "worse": "critical", "meh": "medium"})
    rows = [_row("ok"), _row("noimg", images=()), _row("x", title="ab"), _row("bad"),
            _row("worse"), _row("meh"), _row("closed", shop="dead")]
    kept = [r["id"] for r in service.guest_quality_filter(object(), rows)]
    assert kept == ["ok", "meh"]


def test_category_page_weaves_matching_boosts(monkeypatch):
    import feed.catalog as cat

    organic = [{"id": f"o{i}"} for i in range(30)]
    calls = {}

    def fake_fetch(db, filters, *, select, page, limit, offset=None, **k):
        calls["offset"] = offset
        start = (page - 1) * limit if offset is None else offset
        return organic[start : start + limit], len(organic)

    monkeypatch.setattr(service, "fetch_catalog_page", fake_fetch)
    monkeypatch.setattr(service.boost_slots, "active_boosts", lambda db: [
        {"id": "b_phone", "category": "Phones", "score_bonus": 50, "shop_id": "s"},
        {"id": "b_car", "category": "Cars", "score_bonus": 50, "shop_id": "t"},
    ])
    monkeypatch.setattr(cat, "matching_ids", lambda db, f, ids: {"b_phone"})
    monkeypatch.setattr(service, "_fetch_products_by_ids", lambda db, ids: [{"id": i} for i in ids])
    monkeypatch.setattr(service.C, "BOOST_SLOT_EVERY", 7)
    monkeypatch.setattr(service.C, "BOOST_SLOT_PHASE", 3)
    f = ListingFilters(category="Phones")
    rows, total = service._catalog_page(object(), f, page=1, limit=10)
    ids = [r["id"] for r in rows]
    assert ids[2] == "b_phone" and "b_car" not in ids and ids.count("b_phone") == 1
    assert total == 30
    rows2, _ = service._catalog_page(object(), f, page=2, limit=10)
    assert calls["offset"] == 9  # page 1: 9 organic + 1 boosted slot (one boost -> one slot)
    assert rows2[0]["id"] == "o9"


def test_non_relevance_sort_has_no_boost_slots(monkeypatch):
    monkeypatch.setattr(service, "fetch_catalog_page", lambda db, f, **k: ([{"id": "a"}], 1))
    monkeypatch.setattr(service.boost_slots, "active_boosts", lambda db: (_ for _ in ()).throw(AssertionError))
    rows, _ = service._catalog_page(object(), ListingFilters(category="Phones", sort="newest"), page=1, limit=5)
    assert rows == [{"id": "a"}]
