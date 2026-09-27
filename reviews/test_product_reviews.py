from postgrest.exceptions import APIError

from reviews.product_service import ReviewWriteError, create_product_review


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store: dict):
        self.store = store
        self.op = "select"
        self.filters: dict = {}
        self.payload = None
        self.fail: APIError | None = None

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self.fail is not None and self.op == "insert":
            raise self.fail
        rows = self.store.setdefault("rows", [])
        matched = [
            row for row in rows if all(row.get(key) == value for key, value in self.filters.items())
        ]
        if self.op == "select":
            return _Resp(matched[:1])
        if self.op == "insert":
            row = {"id": "rev-1", **self.payload}
            rows.append(row)
            return _Resp([row])
        for row in matched:
            row.update(self.payload)
            return _Resp([row])
        return _Resp([])


class _Admin:
    def __init__(self):
        self.store: dict = {"rows": []}
        self.query = _Query(self.store)

    def table(self, _name):
        self.query = _Query(self.store)
        self.query.fail = self.fail
        return self.query

    fail: APIError | None = None


def test_second_review_updates_the_first():
    admin = _Admin()
    first = create_product_review("p1", "u1", 4, "ok", client=admin)
    assert first["rating"] == 4
    second = create_product_review("p1", "u1", 2, "changed", client=admin)
    assert second["rating"] == 2
    assert second["comment"] == "changed"
    assert len(admin.store["rows"]) == 1


def test_empty_json_body_keeps_query_rating():
    import asyncio

    from starlette.requests import Request

    from marketplace.routes.product_reviews import _rating_from_request

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/products/p/reviews",
        "raw_path": b"/api/v1/products/p/reviews",
        "query_string": b"rating=4&comment=nice",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    score, text = asyncio.run(_rating_from_request(Request(scope, receive), 4, "nice"))
    assert score == 4
    assert text == "nice"


def test_invalid_rating_is_422():
    try:
        create_product_review("p1", "u1", 0, None, client=_Admin())
    except ReviewWriteError as exc:
        assert exc.status == 422
        assert exc.code == "invalid_rating"
    else:
        raise AssertionError("expected ReviewWriteError")


def test_dropped_rating_column_trigger_is_not_a_500():
    admin = _Admin()
    admin.fail = APIError(
        {
            "message": 'record "new" has no field "average_rating"',
            "code": "42703",
            "hint": None,
            "details": "products.average_rating",
        }
    )
    try:
        create_product_review("p1", "u1", 5, None, client=admin)
    except ReviewWriteError as exc:
        assert exc.status == 503
        assert exc.code == "review_trigger"
    else:
        raise AssertionError("expected ReviewWriteError")
