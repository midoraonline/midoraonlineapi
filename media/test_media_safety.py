"""UploadThing cleanup, key parsing, publish checks, and the dead-image job."""

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from postgrest.exceptions import APIError

from common.events.names import Events
from media.cleanup import cleanup_removed_media
from media.keys import keys_from_urls, removed_file_keys, resolve_stored_keys
from media.references import keys_safe_to_delete, referenced_key_set
from media.sweep import classify_probe, plan_listing_update, run_dead_image_sweep
from media.uploadthing import decode_uploadthing_key, uploadthing_api_key
from media.validate import ProbeResult, assert_published_media, unreachable_media_urls
from shop.events import publish_product_created
from shop.schemas import ProductCreate, ProductUpdate
from shop.service import create_product, update_product

SHARED = "https://sea1.ufs.sh/f/sharedKey"
ONLY = "https://utfs.io/f/onlyKey?download=1"
LOGO = "https://utfs.io/f/logoKey"
AVATAR = "https://abc.ufs.sh/f/avatarKey"
UNSPLASH_DEAD = "https://images.unsplash.com/photo-dead-seed"
LIVE = "https://utfs.io/f/liveKey"


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.payload = None
        self.op = "select"

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def range(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": self.payload.get("id") or f"{self.table}-{len(rows) + 1}", **self.payload}
            rows.append(row)
            return SimpleNamespace(data=[row])
        matched = rows
        for key, value in self.filters:
            matched = [row for row in matched if row.get(key) == value]
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            return SimpleNamespace(data=list(matched))
        return SimpleNamespace(data=list(matched))


class _Client:
    def __init__(self, db=None):
        self.db = db if db is not None else {}

    def table(self, name):
        return _Query(self.db, name)


class _MissingColumnQuery(_Query):
    def execute(self):
        if self.op in {"insert", "update"} and self.payload and "image_keys" in self.payload:
            raise APIError({
                "code": "42703",
                "message": "column image_keys does not exist",
                "details": None,
            })
        return super().execute()


class _MissingColumnClient(_Client):
    def table(self, name):
        return _MissingColumnQuery(self.db, name)


def test_shared_keys_are_not_deleted():
    referenced = referenced_key_set(
        products=[
            {"image_urls": [SHARED], "image_keys": ["sharedKey"], "video_keys": []},
            {"image_urls": [], "image_keys": ["storedOnly"], "video_keys": ["clipKey"]},
        ],
        logos=[LOGO],
        avatars=[AVATAR],
    )
    removed = removed_file_keys(
        [SHARED, ONLY, LOGO, AVATAR, "https://utfs.io/f/storedOnly"],
        [SHARED],
    )
    assert keys_safe_to_delete(removed, referenced) == ["onlyKey"]
    assert "sharedKey" not in keys_safe_to_delete(removed, referenced)
    assert "logoKey" not in keys_safe_to_delete(removed, referenced)
    assert "avatarKey" not in keys_safe_to_delete(removed, referenced)
    assert "storedOnly" not in keys_safe_to_delete(removed, referenced)
    assert "clipKey" not in keys_safe_to_delete(["clipKey", "onlyKey"], referenced)


def test_cleanup_posts_only_unreferenced_keys():
    db = {
        "products": [
            {"id": "keep", "image_urls": [SHARED], "image_keys": ["sharedKey"], "video_keys": []},
        ],
        "shops": [{"logo_url": LOGO}],
        "profiles": [{"avatar_url": AVATAR}],
    }
    deleted: list[list[str]] = []

    async def record(keys):
        deleted.append(list(keys))

    result = asyncio.run(cleanup_removed_media(
        [SHARED, ONLY, LOGO],
        [SHARED],
        client=_Client(db),
        delete_files=record,
    ))
    assert result == ["onlyKey"]
    assert deleted == [["onlyKey"]]


def test_key_backfill_parsing():
    """Same /f/<key> rules as migration 045_product_image_keys.sql."""
    images, videos = keys_from_urls([
        "https://utfs.io/f/abc123",
        "https://foo.ufs.sh/f/vid99.mp4?x=1",
        "https://bar.utfs.io/f/clip/extra",
        "https://images.unsplash.com/photo-1",
        "blob:http://localhost/uuid",
        "https://utfs.io/f/abc123",
        "https://cdn.example/not-uploadthing.jpg",
    ])
    assert images == ["abc123", "vid99.mp4", "clip"]
    assert videos == ["vid99.mp4"]
    assert resolve_stored_keys(
        ["https://cdn.example/a.jpg"],
        client_image_keys=["from-client"],
        client_video_keys=[],
    ) == (["from-client"], [])
    assert resolve_stored_keys(
        ["https://utfs.io/f/fromurl"],
        client_image_keys=["ignored"],
    ) == (["fromurl"], [])
    assert resolve_stored_keys([]) == ([], [])


def test_create_derives_keys_and_falls_back_when_column_missing():
    client = _Client()
    created = create_product(
        client,
        "shop-1",
        ProductCreate(
            title="Phone",
            description="A phone for sale in town today.",
            price_ugx=1000,
            category="Mobile Phones & Tablets",
            image_urls=["https://utfs.io/f/phoneKey", "https://utfs.io/f/tour.mp4"],
        ),
    )
    assert created["id"]
    stored = client.db["products"][0]
    assert stored["image_keys"] == ["phoneKey", "tour.mp4"]
    assert stored["video_keys"] == ["tour.mp4"]

    legacy = _MissingColumnClient()
    create_product(
        legacy,
        "shop-1",
        ProductCreate(
            title="Phone",
            description="A phone for sale in town today.",
            price_ugx=1000,
            category="Mobile Phones & Tablets",
            image_urls=["https://utfs.io/f/phoneKey"],
        ),
    )
    assert "image_keys" not in legacy.db["products"][0]
    assert legacy.db["products"][0]["image_urls"] == ["https://utfs.io/f/phoneKey"]


def test_explicit_empty_image_urls_clears_images_and_keys():
    body = ProductUpdate.model_validate({"image_urls": []})
    dumped = body.model_dump(exclude_unset=True)
    assert dumped["image_urls"] == []

    client = _Client({
        "products": [{
            "id": "p1",
            "shop_id": "shop-1",
            "title": "Lamp",
            "description": "A lamp.",
            "price_ugx": 5000,
            "image_urls": [SHARED],
            "image_keys": ["sharedKey"],
            "video_keys": ["clipKey"],
            "status": "active",
            "is_published": True,
        }],
    })
    updated = update_product(client, "p1", body)
    assert updated["image_urls"] == []
    row = client.db["products"][0]
    assert row["image_urls"] == []
    assert row["image_keys"] == []
    assert row["video_keys"] == []


def test_publish_validation_rejects_bad_links_and_allows_timeouts():
    async def head(url: str) -> ProbeResult:
        if url.endswith("missing"):
            return ProbeResult(status=404, content_type="text/html")
        if url.endswith("page"):
            return ProbeResult(status=200, content_type="text/html")
        if url.endswith("slow"):
            raise httpx.TimeoutException("slow")
        return ProbeResult(status=200, content_type="image/jpeg")

    bad = asyncio.run(unreachable_media_urls([
        "blob:http://localhost/x",
        "data:image/png;base64,aaaa",
        "http://utfs.io/f/abc",
        "https://evil.example/a.jpg",
        "https://utfs.io/f/missing",
        "https://foo.ufs.sh/f/page",
        "https://utfs.io/f/slow",
        "https://images.unsplash.com/photo-ok",
        LIVE,
    ], head=head))
    assert bad == [
        "blob:http://localhost/x",
        "data:image/png;base64,aaaa",
        "http://utfs.io/f/abc",
        "https://evil.example/a.jpg",
        "https://utfs.io/f/missing",
        "https://foo.ufs.sh/f/page",
    ]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(assert_published_media(["https://utfs.io/f/missing"], head=head))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "media_unreachable"
    assert exc.value.detail["bad_urls"] == ["https://utfs.io/f/missing"]


def test_dead_image_job_promotes_cover_and_flags_or_clears():
    product = {
        "id": "prod",
        "item_type": "product",
        "image_urls": [UNSPLASH_DEAD, LIVE],
        "listing_meta": {"color": "red"},
    }
    promoted = plan_listing_update(product, {UNSPLASH_DEAD: "dead", LIVE: "ok"})
    assert promoted["image_urls"] == [LIVE]
    assert "listing_meta" not in promoted
    assert promoted["image_keys"] == ["liveKey"]

    emptied = plan_listing_update(
        {"id": "prod", "item_type": "product", "image_urls": [UNSPLASH_DEAD], "listing_meta": {"color": "red"}},
        {UNSPLASH_DEAD: "dead"},
    )
    assert emptied["image_urls"] == []
    assert emptied["listing_meta"] == {"color": "red", "needs_media": True}

    service = plan_listing_update(
        {"id": "svc", "item_type": "service", "image_urls": [UNSPLASH_DEAD], "listing_meta": {}},
        {UNSPLASH_DEAD: "dead"},
    )
    assert service["image_urls"] == []
    assert "listing_meta" not in service

    for kind in ("opportunity", "job"):
        cleared = plan_listing_update(
            {"id": kind, "item_type": kind, "image_urls": [ONLY], "listing_meta": {"keep": 1}},
            {ONLY: "dead"},
        )
        assert cleared["image_urls"] == []
        assert "needs_media" not in cleared.get("listing_meta", {})

    assert plan_listing_update(
        {"id": "prod", "item_type": "product", "image_urls": [LIVE]},
        {LIVE: "unknown"},
    ) is None
    assert classify_probe(ProbeResult(status=410)) == "dead"
    assert classify_probe(ProbeResult(status=500)) == "ok"
    assert classify_probe(ProbeResult(timed_out=True)) == "unknown"


def test_dead_image_sweep_updates_only_active_listings():
    db = {
        "products": [
            {
                "id": "cover",
                "status": "active",
                "is_published": True,
                "item_type": "product",
                "image_urls": [UNSPLASH_DEAD, LIVE],
                "listing_meta": {},
            },
            {
                "id": "service",
                "status": "active",
                "is_published": True,
                "item_type": "service",
                "image_urls": [UNSPLASH_DEAD],
                "listing_meta": {},
            },
            {
                "id": "needs",
                "status": "active",
                "is_published": True,
                "item_type": "product",
                "image_urls": [ONLY],
                "listing_meta": {"note": "keep"},
            },
            {
                "id": "flaky",
                "status": "active",
                "is_published": True,
                "item_type": "product",
                "image_urls": ["https://utfs.io/f/slowKey"],
                "listing_meta": {},
            },
            {
                "id": "hidden",
                "status": "hidden",
                "is_published": False,
                "item_type": "product",
                "image_urls": [UNSPLASH_DEAD],
                "listing_meta": {},
            },
        ],
    }

    def head(url: str) -> ProbeResult:
        if "slow" in url:
            return ProbeResult(timed_out=True)
        if url in {UNSPLASH_DEAD, ONLY}:
            return ProbeResult(status=404, content_type="text/plain")
        return ProbeResult(status=200, content_type="image/jpeg")

    summary = asyncio.run(run_dead_image_sweep(_Client(db), head=head))
    by_id = {row["id"]: row for row in db["products"]}
    assert by_id["cover"]["image_urls"] == [LIVE]
    assert by_id["service"]["image_urls"] == []
    assert "needs_media" not in (by_id["service"].get("listing_meta") or {})
    assert by_id["needs"]["image_urls"] == []
    assert by_id["needs"]["listing_meta"]["needs_media"] is True
    assert by_id["needs"]["listing_meta"]["note"] == "keep"
    assert by_id["flaky"]["image_urls"] == ["https://utfs.io/f/slowKey"]
    assert by_id["hidden"]["image_urls"] == [UNSPLASH_DEAD]
    assert summary["checked_listings"] == 4
    assert summary["removed_urls"] == 3
    assert summary["cleared_listings"] == 1
    assert summary["flagged_listings"] == 1
    assert summary["updated_listings"] == 3
    assert summary["errors"] == 0


def test_missing_uploadthing_token_skips_delete(monkeypatch, caplog):
    monkeypatch.delenv("UPLOADTHING_TOKEN", raising=False)
    monkeypatch.delenv("UPLOADTHING_SECRET", raising=False)
    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(uploadthing_token="", uploadthing_secret=""),
    )
    with caplog.at_level("WARNING"):
        assert uploadthing_api_key() is None
    assert "UPLOADTHING_TOKEN" in caplog.text

    token = base64.b64encode(json.dumps({"apiKey": "sk_live_from_token"}).encode()).decode()
    assert decode_uploadthing_key(token) == "sk_live_from_token"
    assert decode_uploadthing_key("sk_live_raw") == "sk_live_raw"


def test_saves_return_before_moderation_and_subscribers(monkeypatch):
    events: list[str] = []

    async def emit(name, _payload):
        events.append(name)

    async def scenario():
        monkeypatch.setattr("shop.events.get_event_bus", lambda: SimpleNamespace(emit=emit))
        background = BackgroundTasks()
        await publish_product_created(
            {
                "id": "p1",
                "shop_id": "s1",
                "title": "Lamp",
                "status": "pending_review",
                "image_urls": [],
            },
            seller_id="user-1",
            background=background,
        )
        assert events == [Events.PRODUCT_PENDING_REVIEW]
        await background()
        assert events == [
            Events.PRODUCT_PENDING_REVIEW,
            Events.PRODUCT_CREATED,
            Events.PRODUCT_MODERATE_NOW,
        ]

    asyncio.run(scenario())
