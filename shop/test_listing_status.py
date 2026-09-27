import pytest
from fastapi import HTTPException

from shop.listing_status import apply_listing_status, is_closing_only, stock_required


def test_sold_unpublishes_and_clears_stock():
    payload = {"status": "sold", "stock_quantity": 4, "is_published": True}
    apply_listing_status(payload, {"status": "active", "item_type": "product"}, content_changed=False)
    assert payload["status"] == "sold"
    assert payload["is_published"] is False
    assert payload["stock_quantity"] == 0


@pytest.mark.parametrize("status", ["unavailable", "filled", "closed"])
def test_other_close_actions(status: str):
    payload = {"status": status}
    apply_listing_status(payload, {"status": "active", "item_type": "job"}, content_changed=False)
    assert payload["is_published"] is False
    assert payload["stock_quantity"] == 0
    assert is_closing_only({"status": status}) is True


def test_reactivate_product_requires_new_stock():
    payload = {"status": "active"}
    with pytest.raises(HTTPException) as exc:
        apply_listing_status(
            payload,
            {"status": "sold", "item_type": "product", "stock_quantity": 0},
            content_changed=False,
        )
    assert exc.value.detail["code"] == "stock_required"


def test_reactivate_product_with_stock():
    payload = {"status": "active", "stock_quantity": 2}
    apply_listing_status(
        payload,
        {"status": "unavailable", "item_type": "product", "stock_quantity": 0},
        content_changed=False,
    )
    assert payload["status"] == "active"
    assert payload["is_published"] is True
    assert payload["stock_quantity"] == 2


def test_service_can_reactivate_without_stock():
    payload = {"status": "active"}
    apply_listing_status(
        payload,
        {"status": "filled", "item_type": "service", "stock_quantity": 0},
        content_changed=False,
    )
    assert payload["status"] == "active"
    assert payload["is_published"] is True
    assert stock_required("service") is False
    assert stock_required("product") is True


def test_merchant_cannot_self_approve():
    payload = {"status": "active"}
    apply_listing_status(payload, {"status": "pending_review", "item_type": "product"}, content_changed=False)
    assert "status" not in payload


def test_content_edit_returns_to_review():
    payload = {"title": "New title"}
    apply_listing_status(payload, {"status": "active", "item_type": "product"}, content_changed=True)
    assert payload["status"] == "pending_review"


def test_invalid_status():
    with pytest.raises(HTTPException) as exc:
        apply_listing_status({"status": "banana"}, {"status": "active"}, content_changed=False)
    assert exc.value.status_code == 422
