"""Catalog filters run in the query before pagination."""

from types import SimpleNamespace

import pytest

from feed.catalog import (
    AVAILABLE_OR,
    PHASE3_BADGES,
    ListingFilters,
    build_listing_filters,
    fetch_catalog_page,
)

CARD_SELECT = (
    "id,shop_id,title,category,item_type,price_ugx,stock_quantity,"
    "is_published,status,created_at"
)


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = len(data) if count is None else count


class FakeQuery:
    def __init__(self, table_name, client):
        self.table_name = table_name
        self.client = client
        self.ops: list[tuple] = []

    def execute(self):
        self.ops.append(("execute", (), {}))
        spec = self.client.results.get(self.table_name, _Resp([]))
        if callable(spec):
            return spec(self)
        return spec

    def __getattr__(self, name):
        if name == "not_":
            self.ops.append(("not_", (), {}))
            return self

        def _call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return _call


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.queries: list[FakeQuery] = []

    def table(self, name):
        query = FakeQuery(name, self)
        self.queries.append(query)
        return query


def _product_query(client: FakeClient) -> FakeQuery:
    ranged = [
        q for q in client.queries
        if q.table_name == "products" and any(op[0] == "range" for op in q.ops)
    ]
    assert ranged, "expected a paginated products query"
    return ranged[-1]


def _op_names(query: FakeQuery) -> list[str]:
    return [op[0] for op in query.ops]


def test_default_filters_leave_ranking_in_place():
    assert not ListingFilters().is_active()
    assert not build_listing_filters().is_active()


def test_verified_only_uses_shop_ids_before_range():
    client = FakeClient(
        {
            "shop_verifications": _Resp([{"shop_id": "shop-a"}]),
            "shops": _Resp([{"id": "shop-b"}]),
            "products": _Resp(
                [{"id": "p1", "shop_id": "shop-a", "title": "A", "price_ugx": 10, "stock_quantity": 2, "created_at": "2024-01-02"}],
                count=4,
            ),
        }
    )
    rows, total = fetch_catalog_page(
        client,
        build_listing_filters(verified_only=True, sort="newest"),
        select=CARD_SELECT,
        page=1,
        limit=2,
    )
    shops = [q for q in client.queries if q.table_name == "shops"]
    assert shops
    overlap = next(op for op in shops[0].ops if op[0] == "overlaps")
    assert list(overlap[1][1]) == list(PHASE3_BADGES)
    ver = next(q for q in client.queries if q.table_name == "shop_verifications")
    or_filter = next(op for op in ver.ops if op[0] == "or_")
    assert "stage2_status.eq.verified" in or_filter[1][0]
    assert "stage4_status.eq.verified" in or_filter[1][0]

    product = _product_query(client)
    names = _op_names(product)
    assert names.index("in_") < names.index("range")
    shop_in = next(op for op in product.ops if op[0] == "in_" and op[1][0] == "shop_id")
    assert set(shop_in[1][1]) == {"shop-a", "shop-b"}
    select = next(op for op in product.ops if op[0] == "select")
    assert "updated_at" not in select[1][0]
    assert total == 4
    assert rows[0]["id"] == "p1"


def test_verified_only_with_no_badges_skips_product_fetch():
    client = FakeClient(
        {
            "shop_verifications": _Resp([]),
            "shops": _Resp([]),
            "products": _Resp([{"id": "should-not-load"}], count=9),
        }
    )
    rows, total = fetch_catalog_page(
        client,
        build_listing_filters(verified_only=True),
        select=CARD_SELECT,
        page=1,
        limit=20,
    )
    assert rows == []
    assert total == 0
    assert not any(q.table_name == "products" for q in client.queries)


def test_large_verified_set_uses_badge_overlap_on_the_product_query():
    client = FakeClient(
        {
            "shop_verifications": _Resp([{"shop_id": f"s{i}"} for i in range(121)]),
            "shops": _Resp([]),
            "products": _Resp([], count=0),
        }
    )
    fetch_catalog_page(
        client,
        build_listing_filters(verified_only=True, sort="newest"),
        select=CARD_SELECT,
        page=1,
        limit=10,
    )
    product = _product_query(client)
    select = next(op for op in product.ops if op[0] == "select")
    assert "shops!inner(trust_badges)" in select[1][0]
    assert "updated_at" not in select[1][0]
    overlap = next(op for op in product.ops if op[0] == "overlaps")
    assert overlap[1][0] == "shops.trust_badges"
    assert list(overlap[1][1]) == list(PHASE3_BADGES)
    assert not any(op[0] == "in_" and op[1][0] == "shop_id" for op in product.ops)
    assert _op_names(product).index("overlaps") < _op_names(product).index("range")


def test_available_and_price_and_sort_apply_before_range():
    client = FakeClient({"products": _Resp([], count=3)})
    fetch_catalog_page(
        client,
        build_listing_filters(available=True, min_price=1000, max_price=5000, sort="price_asc"),
        select=CARD_SELECT,
        page=2,
        limit=10,
    )
    product = _product_query(client)
    names = _op_names(product)
    assert ("eq", ("status", "active"), {}) in [(op[0], op[1], op[2]) for op in product.ops]
    assert any(op[0] == "or_" and op[1][0] == AVAILABLE_OR for op in product.ops)
    assert any(op[0] == "gte" and op[1] == ("price_ugx", 1000) for op in product.ops)
    assert any(op[0] == "lte" and op[1] == ("price_ugx", 5000) for op in product.ops)
    orders = [op for op in product.ops if op[0] == "order"]
    assert orders[0][1] == ("price_ugx",)
    assert orders[0][2] == {"desc": False}
    assert names.index("or_") < names.index("range")
    assert names.index("order") < names.index("range")
    range_op = next(op for op in product.ops if op[0] == "range")
    assert range_op[1] == (10, 19)


def test_listing_type_opportunity_and_job():
    client = FakeClient({"products": _Resp([], count=0)})
    fetch_catalog_page(
        client,
        build_listing_filters(listing_type="opportunity"),
        select=CARD_SELECT,
        page=1,
        limit=5,
    )
    product = _product_query(client)
    listed = next(op for op in product.ops if op[0] == "in_" and op[1][0] == "item_type")
    assert listed[1][1] == ["opportunity", "job"]

    client = FakeClient({"products": _Resp([], count=0)})
    fetch_catalog_page(
        client,
        build_listing_filters(listing_type="job", opportunity_kind="gig"),
        select=CARD_SELECT,
        page=1,
        limit=5,
    )
    product = _product_query(client)
    job_or = next(op for op in product.ops if op[0] == "or_" and "item_type.eq.job" in op[1][0])
    assert "opportunity_kind.eq.job" in job_or[1][0]
    assert any(
        op[0] == "eq" and op[1] == ("listing_meta->>opportunity_kind", "gig")
        for op in product.ops
    )
    assert _op_names(product).index("or_") < _op_names(product).index("range")


def test_category_parent_expands_before_range():
    client = FakeClient({"products": _Resp([], count=1)})
    fetch_catalog_page(
        client,
        build_listing_filters(category="Electronics"),
        select=CARD_SELECT,
        page=1,
        limit=5,
    )
    product = _product_query(client)
    labels = next(op for op in product.ops if op[0] == "in_" and op[1][0] == "category")
    assert "Electronics" in labels[1][1]
    assert "Audio & Headphones" in labels[1][1]
    assert _op_names(product).index("in_") < _op_names(product).index("range")


def test_listing_meta_and_location_before_range():
    client = FakeClient(
        {
            "shops": _Resp([{"id": "shop-loc"}]),
            "products": _Resp([], count=0),
        }
    )
    fetch_catalog_page(
        client,
        build_listing_filters(
            location="Kampala",
            pricing_model="hourly",
            compensation="paid",
            sort="most_viewed",
        ),
        select=CARD_SELECT,
        page=1,
        limit=5,
    )
    product = _product_query(client)
    loc = next(op for op in product.ops if op[0] == "or_" and "location_name" in op[1][0])
    assert "shop-loc" in loc[1][0]
    assert "Kampala" in loc[1][0]
    assert any(op[0] == "eq" and op[1][0] == "listing_meta->>pricing_model" for op in product.ops)
    assert any(op[0] == "eq" and op[1][0] == "listing_meta->>compensation" for op in product.ops)
    assert _op_names(product).index("or_") < _op_names(product).index("range")


def test_min_rating_constrains_product_ids_before_range():
    client = FakeClient(
        {
            "product_reviews": _Resp(
                [
                    {"product_id": "high", "rating": 5},
                    {"product_id": "high", "rating": 4},
                    {"product_id": "low", "rating": 2},
                ]
            ),
            "products": _Resp([], count=1),
        }
    )
    fetch_catalog_page(
        client,
        build_listing_filters(min_rating=4, sort="newest"),
        select=CARD_SELECT,
        page=1,
        limit=10,
    )
    product = _product_query(client)
    ids = next(op for op in product.ops if op[0] == "in_" and op[1][0] == "id")
    assert ids[1][1] == ["high"]
    assert _op_names(product).index("in_") < _op_names(product).index("range")


def test_trust_score_orders_in_the_query_before_range():
    client = FakeClient({"products": _Resp([], count=2)})
    fetch_catalog_page(
        client,
        build_listing_filters(sort="trust_score"),
        select=CARD_SELECT,
        page=1,
        limit=5,
    )
    product = _product_query(client)
    select = next(op for op in product.ops if op[0] == "select")
    assert "shops!inner(trust_score)" in select[1][0]
    assert "updated_at" not in select[1][0]
    order = next(op for op in product.ops if op[0] == "order")
    assert order[1] == ("trust_score",)
    assert order[2]["foreign_table"] == "shops"
    assert order[2]["desc"] is True
    assert _op_names(product).index("order") < _op_names(product).index("range")


def test_invalid_sort_and_listing_type():
    with pytest.raises(ValueError):
        build_listing_filters(sort="updated")
    with pytest.raises(ValueError):
        build_listing_filters(listing_type="vehicle")
    with pytest.raises(ValueError):
        build_listing_filters(min_price=20, max_price=10)
