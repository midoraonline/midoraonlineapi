"""Category field definitions: legacy reads, inheritance, and listing checks."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.factory.errors import register_exception_handlers
from categories.fields import missing_required_fields, present_categories, stored_from_api
from categories.router import router as categories_router
from categories.schemas import CategoryUpdateRequest
from categories.service import (
    add_category_field,
    metadata_for_storage,
    assert_required_listing_fields,
    delete_category_field,
    invalidate_categories_cache,
    reorder_category_fields,
    update_category_field,
)
from db.supabase import get_supabase_client
from shop.routes.products import _enforce_publish_gates
from shop.schemas import ProductCreate, ProductUpdate


PARENT = {
    "slug": "electronics",
    "label": "Electronics",
    "sort_order": 3,
    "parent_slug": None,
    "metadata": [
        {"key": "brand", "label": "Brand", "kind": "text", "required": False},
        {"name": "model", "title": "Model", "type": "text"},
    ],
}

CHILD = {
    "slug": "mobile-phones-and-tablets",
    "label": "Mobile Phones & Tablets",
    "sort_order": 301,
    "parent_slug": "electronics",
    "metadata": [
        {"key": "brand", "required": True},
        {
            "key": "storage",
            "label": "Storage",
            "type": "select",
            "required": True,
            "options": ["64GB", "128GB"],
            "help": "Capacity",
        },
    ],
}


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def limit(self, _n):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self._op == "insert":
            row = dict(self._payload)
            rows.append(row)
            return SimpleNamespace(data=[row])
        matched = [row for row in rows if all(check(row) for check in self._filters)]
        if self._op == "update":
            for row in matched:
                row.update(self._payload)
            return SimpleNamespace(data=[dict(row) for row in matched])
        if self._op == "delete":
            self.db[self.table] = [row for row in rows if row not in matched]
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=[dict(row) for row in matched])


class _Client:
    def __init__(self, categories=None):
        self.db = {"categories": categories if categories is not None else [dict(PARENT), dict(CHILD)]}

    def table(self, name):
        return _Query(self.db, name)


@pytest.fixture(autouse=True)
def _clear_category_cache():
    invalidate_categories_cache()
    yield
    invalidate_categories_cache()


def _by_key(fields):
    return {field["key"]: field for field in fields}


def test_category_patch_keeps_help_alias():
    body = CategoryUpdateRequest.model_validate(
        {
            "metadata": [
                {
                    "key": "brand",
                    "label": "Brand",
                    "type": "text",
                    "help": "Shown on the Post Item form",
                }
            ]
        }
    )
    stored = metadata_for_storage([field.model_dump() for field in body.metadata])
    assert stored[0]["help_text"] == "Shown on the Post Item form"
    assert body.metadata[0].help_text == "Shown on the Post Item form"


def test_legacy_shapes_load_as_field_definitions():
    rows = present_categories(
        [
            {
                "slug": "electronics",
                "label": "Electronics",
                "sort_order": 3,
                "parent_slug": None,
                "metadata": {
                    "brand": {"label": "Brand", "kind": "text", "required": "true"},
                    "color": "Color",
                },
            }
        ]
    )
    fields = _by_key(rows[0]["fields"])
    assert fields["brand"]["type"] == "text"
    assert fields["brand"]["kind"] == "text"
    assert fields["brand"]["required"] is True
    assert fields["color"]["label"] == "Color"
    assert rows[0]["metadata"][0]["key"] == "brand"


def test_subcategory_inherits_parent_fields_and_can_override_required():
    presented = present_categories([dict(PARENT), dict(CHILD)])
    child = next(row for row in presented if row["slug"] == "mobile-phones-and-tablets")
    own = _by_key(child["fields"])
    effective = _by_key(child["effective_fields"])

    assert own["brand"]["partial"] is True
    assert own["brand"]["required"] is True
    assert effective["brand"]["label"] == "Brand"
    assert effective["brand"]["type"] == "text"
    assert effective["brand"]["required"] is True
    assert effective["brand"]["inherited"] is True
    assert effective["model"]["required"] is False
    assert effective["model"]["inherited"] is True
    assert effective["storage"]["type"] == "select"
    assert effective["storage"]["inherited"] is False
    assert effective["storage"]["options"] == [
        {"value": "64GB", "label": "64GB"},
        {"value": "128GB", "label": "128GB"},
    ]
    assert effective["storage"]["help_text"] == "Capacity"
    stored = {item["key"]: item for item in stored_from_api(child["effective_fields"])}
    assert set(stored) == {"brand", "storage"}
    assert stored["brand"] == {"key": "brand", "required": True}
    assert stored["storage"]["label"] == "Storage"


def test_admin_can_add_update_reorder_and_delete_fields():
    client = _Client()
    created = add_category_field(
        client,
        "electronics",
        {
            "label": "Warranty months",
            "type": "number",
            "required": False,
            "help_text": "Leave blank if unknown",
        },
    )
    warranty = _by_key(created["fields"])["warranty_months"]
    assert warranty["type"] == "number"
    assert warranty["help_text"] == "Leave blank if unknown"

    updated = update_category_field(client, "electronics", "brand", {"required": True})
    assert _by_key(updated["fields"])["brand"]["required"] is True
    stored_brand = next(
        field for field in client.db["categories"][0]["metadata"] if field.get("key") == "brand"
    )
    assert stored_brand["kind"] == "text"
    assert stored_brand["required"] is True
    assert stored_brand["label"] == "Brand"

    reordered = reorder_category_fields(
        client, "electronics", ["warranty_months", "model", "brand"]
    )
    assert [field["key"] for field in reordered["fields"]] == [
        "warranty_months",
        "model",
        "brand",
    ]

    removed = delete_category_field(client, "electronics", "warranty_months")
    assert "warranty_months" not in _by_key(removed["fields"])


def test_required_override_does_not_copy_the_parent_field():
    client = _Client()
    update_category_field(
        client, "mobile-phones-and-tablets", "model", {"required": True}
    )
    child = next(row for row in client.db["categories"] if row["slug"] == "mobile-phones-and-tablets")
    stored = _by_key(child["metadata"])
    assert stored["model"] == {"key": "model", "required": True}
    assert "label" not in stored["model"]


def test_public_fields_endpoint_returns_merged_fields():
    client = _Client()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(categories_router, prefix="/api/v1")
    app.dependency_overrides[get_supabase_client] = lambda: client
    api = TestClient(app)

    listed = api.get("/api/v1/categories")
    assert listed.status_code == 200
    phones = next(
        item for item in listed.json()["items"] if item["slug"] == "mobile-phones-and-tablets"
    )
    assert phones["metadata"][0]["key"] == "brand"
    assert phones["fields"][0]["required"] is True

    effective = api.get("/api/v1/categories/mobile-phones-and-tablets/fields")
    assert effective.status_code == 200
    body = effective.json()
    assert [field["key"] for field in body["fields"]] == ["brand", "model", "storage"]
    assert body["fields"][0]["required"] is True
    assert body["fields"][0]["inherited"] is True
    assert body["fields"][1]["label"] == "Model"


def test_missing_required_fields_are_a_422_and_drafts_skip_them(monkeypatch):
    monkeypatch.setattr("shop.publish_gates.assert_can_publish", lambda *args, **kwargs: None)
    client = _Client()
    with pytest.raises(HTTPException) as exc:
        assert_required_listing_fields(client, "Mobile Phones & Tablets", {"model": "A1"})
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "listing_fields_required"
    assert exc.value.detail["missing_fields"] == [
        {"key": "brand", "label": "Brand"},
        {"key": "storage", "label": "Storage"},
    ]

    assert_required_listing_fields(
        client,
        "Mobile Phones & Tablets",
        {"brand": "Nokia", "storage": "64GB"},
    )
    assert missing_required_fields(
        [
            {"key": "qty", "label": "Qty", "required": True},
            {"key": "used", "label": "Used", "required": True},
        ],
        {"qty": 0, "used": False},
    ) == []

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/check")
    def check():
        assert_required_listing_fields(client, "Mobile Phones & Tablets", {})

    response = TestClient(app).post("/check")
    assert response.status_code == 422
    assert response.json()["code"] == "listing_fields_required"
    assert response.json()["missing_fields"][0]["key"] == "brand"

    body = ProductCreate(
        title="Phone",
        price_ugx=1000,
        image_urls=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
        category="Mobile Phones & Tablets",
        location_name="Kampala",
        listing_meta={"brand": "Nokia"},
        is_published=True,
    )
    with pytest.raises(HTTPException) as publish_exc:
        asyncio.run(_enforce_publish_gates(client, user_id="user-1", shop_id="shop-1", body_or_row=body))
    assert publish_exc.value.status_code == 422

    draft = ProductUpdate(is_published=False, listing_meta={})
    asyncio.run(_enforce_publish_gates(
        client,
        user_id="user-1",
        shop_id="shop-1",
        body_or_row=draft,
        existing={
            "is_published": True,
            "category": "Mobile Phones & Tablets",
            "listing_meta": {},
            "image_urls": ["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
            "price_ugx": 1000,
            "item_type": "product",
            "location_name": "Kampala",
            "description": "A phone",
        },
    ))

    async def _reachable(urls):
        from media.validate import ProbeResult

        return {url: ProbeResult(status=200, content_type="image/jpeg") for url in urls}

    monkeypatch.setattr("media.validate.head_media_urls", _reachable)
    filled = ProductUpdate(listing_meta={"brand": "Nokia", "storage": "128GB"})
    asyncio.run(_enforce_publish_gates(
        client,
        user_id="user-1",
        shop_id="shop-1",
        body_or_row=filled,
        existing={
            "is_published": True,
            "category": "Mobile Phones & Tablets",
            "listing_meta": {},
            "image_urls": ["https://utfs.io/f/a", "https://utfs.io/f/b"],
            "price_ugx": 1000,
            "item_type": "product",
            "location_name": "Kampala",
            "description": "A phone",
        },
    ))
