"""Card lists expose a short description and listing_meta for text-only cards."""

from types import SimpleNamespace

from feed.composite import get_home_feed
from feed.service import _PRODUCT_CARD_SELECT, _to_response
from search.service import _PRODUCT_FIELDS, _attach_shops
from shop.schemas import ProductResponse
from shop.service import get_similar_products


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def neq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) != value)
        return self

    def in_(self, key, values):
        wanted = set(values)
        self._filters.append(lambda row, key=key, wanted=wanted: row.get(key) in wanted)
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        matched = [row for row in self.rows if all(check(row) for check in self._filters)]
        return SimpleNamespace(data=matched)


class _Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def test_feed_card_select_includes_description_and_listing_meta():
    assert "description" in _PRODUCT_CARD_SELECT.split(",")
    assert "listing_meta" in _PRODUCT_CARD_SELECT.split(",")
    assert "description" in _PRODUCT_FIELDS
    assert "listing_meta" in _PRODUCT_FIELDS
    card = _to_response(
        {
            "id": "p1",
            "shop_id": "s1",
            "title": "Tutor",
            "description": "d" * 500,
            "price_ugx": 0,
            "stock_quantity": 0,
            "image_urls": [],
            "category": "Education & Tutoring",
            "is_published": True,
            "item_type": "service",
            "listing_meta": {"pricing_model": "hourly"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert len(card.description) == 300
    assert card.listing_meta["pricing_model"] == "hourly"


def test_home_feed_cards_include_clipped_description_and_listing_meta(monkeypatch):
    product = ProductResponse(
        id="p1",
        shop_id="s1",
        title="House help",
        description="m" * 420,
        price_ugx=0,
        stock_quantity=0,
        image_urls=[],
        category="Maids & Domestic Work",
        is_published=True,
        item_type="opportunity",
        listing_meta={"opportunity_kind": "maids", "compensation": "paid"},
        created_at="2026-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        "feed.service.get_algorithm_feed",
        lambda *_args, **_kwargs: ([product], False, 1),
    )
    monkeypatch.setattr("feed.composite.get_supabase_admin", lambda: _Client({}))
    payload = get_home_feed(limit=10, page=2)
    card = payload["algorithm"][0]
    assert len(card["description"]) == 300
    assert card["listing_meta"]["opportunity_kind"] == "maids"


def test_search_cards_include_description_and_listing_meta():
    client = _Client({})
    cards = _attach_shops(
        client,
        [
            {
                "id": "p1",
                "shop_id": "s1",
                "title": "Cleaning",
                "description": "c" * 350,
                "price_ugx": 0,
                "category": "Cleaning Services",
                "item_type": "service",
                "listing_meta": {"pricing_model": "quote"},
            }
        ],
    )
    assert len(cards[0]["description"]) == 300
    assert cards[0]["listing_meta"]["pricing_model"] == "quote"


def test_similar_products_return_description_and_listing_meta():
    client = _Client(
        {
            "products": [
                {"id": "self", "category": "Cleaning Services", "shop_id": "s1"},
                {
                    "id": "other",
                    "shop_id": "s1",
                    "title": "Deep clean",
                    "description": "s" * 340,
                    "price_ugx": 20000,
                    "category": "Cleaning Services",
                    "item_type": "service",
                    "is_published": True,
                    "status": "active",
                    "listing_meta": {"pricing_model": "starting_at"},
                    "image_urls": [],
                    "created_at": "2026-01-02T00:00:00+00:00",
                },
            ]
        }
    )
    cards = get_similar_products(client, "self", limit=8)
    assert len(cards) == 1
    assert len(cards[0]["description"]) == 300
    assert cards[0]["listing_meta"]["pricing_model"] == "starting_at"
