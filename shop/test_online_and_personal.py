"""Online location publish, physical location checks, and shop-less posting."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from payments.plan_service import assert_can_create_product, assert_can_create_shop
from shop.locations import (
    apply_online_location,
    listing_within_radius,
    shops_matching_radius,
)
from shop.personal import ShopChoiceRequired, resolve_shop_for_direct_post
from shop.publish_gates import assert_can_publish, location_is_usable
from shop.seller_display import apply_seller_profile
from shop.service import create_product
from shop.schemas import ProductCreate
from tenants.schemas import ShopCreate


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *_args, count=None):
        self._op = "select"
        self._count = count == "exact"
        return self

    def eq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def in_(self, key, values):
        wanted = set(values)
        self._filters.append(lambda row, key=key, wanted=wanted: row.get(key) in wanted)
        return self

    def limit(self, _n):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, *_args):
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self._op == "insert":
            row = {"id": self._payload.get("id") or f"{self.table}-{len(rows) + 1}", **self._payload}
            rows.append(row)
            return SimpleNamespace(data=[row], count=1)
        matched = [row for row in rows if all(check(row) for check in self._filters)]
        if self._op == "update":
            for row in matched:
                row.update(self._payload)
            return SimpleNamespace(data=matched, count=len(matched))
        return SimpleNamespace(data=matched, count=len(matched))


class _Client:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return _Query(self.db, name)


def _publish(client, location_name, shop_location="Kampala"):
    client.db["users"] = [{"id": "user-1", "phone_verified": True}]
    client.db["shops"] = [{"id": "shop-1", "location": shop_location}]
    assert_can_publish(
        client,
        user_id="user-1",
        shop_id="shop-1",
        image_urls=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
        price_ugx=15000,
        item_type="product",
        location_name=location_name,
        category="Phones",
        description="Solid phone with a clear battery. Pickup is available in town.",
    )


def test_online_location_is_usable_without_coordinates():
    assert location_is_usable("Online", None) is True
    assert location_is_usable("online", None) is True
    assert location_is_usable("remote", None) is True
    assert location_is_usable(None, {"display": "Online"}) is True
    assert location_is_usable("Kampala", None) is True
    assert location_is_usable(None, "Uganda") is False
    assert location_is_usable(None, {"country": "Uganda"}) is False
    assert location_is_usable("", None) is False


def test_publish_accepts_online_and_still_rejects_country_only():
    _publish(_Client(), "Online", shop_location=None)
    _publish(_Client(), "Kampala", shop_location=None)
    with pytest.raises(HTTPException) as exc:
        _publish(_Client(), "Uganda", shop_location=None)
    assert exc.value.detail["code"] == "location_required"
    with pytest.raises(HTTPException) as exc:
        _publish(_Client(), None, shop_location="Uganda")
    assert exc.value.detail["code"] == "location_required"


def test_frontend_sends_is_online_without_coordinates():
    """PR #73 create/update body: location_name + is_online, no lat/lng."""
    client = _Client()
    online = create_product(
        client,
        "shop-1",
        ProductCreate(
            title="Remote design help",
            description="Brand work for small teams.",
            price_ugx=0,
            category="Tech & IT Services",
            item_type="service",
            location_name="Online",
            is_online=True,
            image_urls=[],
        ),
    )
    assert online["location_name"] == "Online"
    assert online["is_online"] is True
    assert online["listing_meta"]["is_online"] is True
    assert "lat" not in online["listing_meta"]
    assert "lng" not in online["listing_meta"]

    physical = create_product(
        client,
        "shop-1",
        ProductCreate(
            title="Phone in town",
            description="A phone.",
            price_ugx=20000,
            category="Mobile Phones & Tablets",
            item_type="product",
            location_name="Kampala",
            is_online=False,
            image_urls=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
        ),
    )
    assert physical["location_name"] == "Kampala"
    assert physical["is_online"] is False
    assert physical["listing_meta"]["is_online"] is False

    legacy = create_product(
        client,
        "shop-1",
        ProductCreate(
            title="Old online wording",
            description="Still remote.",
            price_ugx=0,
            category="Tech & IT Services",
            item_type="service",
            location_name="Online Shop",
            image_urls=[],
        ),
    )
    assert legacy["location_name"] == "Online"
    assert legacy["is_online"] is True


def test_update_is_online_flag_is_not_a_column():
    from shop.schemas import ProductUpdate
    from shop.service import update_product

    client = _Client()
    client.db["products"] = [{
        "id": "p1",
        "shop_id": "shop-1",
        "title": "Desk",
        "description": "A desk.",
        "price_ugx": 10000,
        "location_name": "Kampala",
        "listing_meta": {"note": "keep"},
        "image_urls": [],
        "is_published": True,
        "status": "active",
    }]
    updated = update_product(
        client,
        "p1",
        ProductUpdate(location_name="Online", is_online=True),
    )
    row = client.db["products"][0]
    assert updated["location_name"] == "Online"
    assert updated["is_online"] is True
    assert "is_online" not in row
    assert row["listing_meta"]["is_online"] is True
    assert row["listing_meta"]["note"] == "keep"

    update_product(client, "p1", ProductUpdate(location_name="Entebbe", is_online=False))
    assert row["location_name"] == "Entebbe"
    assert row["listing_meta"]["is_online"] is False


def test_online_listing_stored_without_coordinates():
    client = _Client()
    created = create_product(
        client,
        "shop-1",
        ProductCreate(
            title="Remote design",
            description="Help with brand work.",
            price_ugx=50000,
            category="Tech & IT Services",
            item_type="service",
            location_name="online",
            image_urls=[],
            listing_meta={"pricing_model": "quote", "lat": 0.3, "lng": 32.5},
        ),
    )
    assert created["location_name"] == "Online"
    assert created["is_online"] is True
    assert created["listing_meta"]["is_online"] is True
    assert created["listing_meta"]["remote"] is True
    assert created["listing_meta"]["pricing_model"] == "quote"
    assert "lat" not in created["listing_meta"]
    assert "lng" not in created["listing_meta"]


def test_switching_away_from_online_clears_the_flag():
    name, meta = apply_online_location("Kampala", {"is_online": True, "remote": True, "note": "x"})
    assert name == "Kampala"
    assert meta["is_online"] is False
    assert meta["remote"] is False
    assert meta["note"] == "x"


def test_near_me_skips_online_listings_and_bad_coordinates():
    shops = [
        {"id": "online-text", "location": "Online"},
        {"id": "online-display", "location": {"display": "Online", "lat": 0.35, "lng": 32.58}},
        {"id": "null-coords", "location": {"lat": None, "lng": None}},
        {"id": "bad-coords", "location": {"lat": "nope", "lng": 32}},
        {"id": "kampala", "location": {"display": "Kampala", "lat": 0.3476, "lng": 32.5825}},
    ]
    assert shops_matching_radius(shops, 0.35, 32.58, 25) == ["kampala"]
    assert listing_within_radius(
        {"location_name": "Online", "listing_meta": {"is_online": True}},
        shops[-1]["location"],
        0.35,
        32.58,
        50,
    ) is False
    assert listing_within_radius(
        {"location_name": "Kampala", "listing_meta": {}},
        shops[-1]["location"],
        0.35,
        32.58,
        25,
    ) is True


def test_post_without_shop_creates_one_personal_profile():
    client = _Client()
    client.db["users"] = [{"id": "user-1", "full_name": "Amina", "phone_verified": True, "phone_number": "+256700"}]
    first, created = resolve_shop_for_direct_post(client, "user-1")
    second, created_again = resolve_shop_for_direct_post(client, "user-1")
    assert created is True
    assert created_again is False
    assert first == second
    assert len(client.db["shops"]) == 1
    assert client.db["shops"][0]["is_personal"] is True
    assert client.db["shops"][0]["is_active"] is False
    assert client.db["shops"][0]["name"] == "Amina"


def test_post_without_shop_reuses_a_single_real_shop():
    client = _Client()
    client.db["shops"] = [{"id": "real-1", "owner_id": "user-1", "is_personal": False}]
    shop_id, created = resolve_shop_for_direct_post(client, "user-1")
    assert shop_id == "real-1"
    assert created is False
    assert len(client.db["shops"]) == 1


def test_post_without_shop_asks_when_several_shops_exist():
    client = _Client()
    client.db["shops"] = [
        {"id": "real-1", "owner_id": "user-1", "is_personal": False},
        {"id": "real-2", "owner_id": "user-1", "is_personal": False},
    ]
    with pytest.raises(ShopChoiceRequired):
        resolve_shop_for_direct_post(client, "user-1")


def test_personal_profile_does_not_consume_shop_allowance(monkeypatch):
    client = _Client()
    client.db["users"] = [{"id": "user-1", "plan_tier": "basic"}]
    client.db["shops"] = [{"id": "personal-1", "owner_id": "user-1", "is_personal": True}]
    assert_can_create_shop(client, "user-1")

    monkeypatch.setattr("auth.service.promote_to_merchant", lambda _user: ("merchant", True))
    monkeypatch.setattr("tenants.service.revalidate_nextjs_cache_tag", lambda *_a, **_k: None)
    monkeypatch.setattr("tenants.service.get_shop", lambda *_a, **_k: {"id": "personal-1"})
    from tenants.service import create_shop

    create_shop(client, "user-1", ShopCreate(name="Amina Store", slug="amina-store"))
    row = client.db["shops"][0]
    assert row["id"] == "personal-1"
    assert row["is_personal"] is False
    assert row["name"] == "Amina Store"
    assert row["slug"] == "amina-store"
    assert len(client.db["shops"]) == 1


def test_listing_cap_and_identity_follow_the_user():
    client = _Client()
    client.db["users"] = [{"id": "user-1", "plan_tier": "basic"}]
    client.db["shops"] = [
        {"id": "personal-1", "owner_id": "user-1", "is_personal": True, "trust_badges": []},
        {"id": "other", "owner_id": "user-1", "is_personal": False, "trust_badges": ["identity_verified"]},
    ]
    client.db["products"] = [
        {"id": f"p{i}", "shop_id": "personal-1"} for i in range(4)
    ]
    assert_can_create_product(client, "personal-1", "user-1")

    client.db["products"].append({"id": "p4", "shop_id": "personal-1"})
    assert_can_create_product(client, "personal-1", "user-1")

    client.db["shops"][1]["trust_badges"] = []
    with pytest.raises(PermissionError):
        assert_can_create_product(client, "personal-1", "user-1")


def test_personal_seller_card_uses_the_user_profile():
    shop = {
        "id": "personal-1",
        "name": "Seller",
        "is_personal": True,
        "trust_badges": ["shop_listed"],
        "whatsapp_number": None,
        "logo_url": None,
        "created_at": "2024-01-01",
        "last_seen_at": None,
    }
    apply_seller_profile(
        shop,
        {
            "full_name": "Amina",
            "phone_verified": True,
            "phone_number": "+256700",
            "created_at": "2020-05-01",
            "last_seen_at": "2026-09-01",
            "avatar_url": "https://cdn.example/a.png",
        },
    )
    assert shop["name"] == "Amina"
    assert shop["seller_name"] == "Amina"
    assert shop["joined_at"] == "2020-05-01"
    assert shop["last_active_at"] == "2026-09-01"
    assert "phone_verified" in shop["trust_badges"]
    assert "shop_listed" not in shop["trust_badges"]
    assert shop["whatsapp_number"] == "+256700"
    assert shop["logo_url"] == "https://cdn.example/a.png"
