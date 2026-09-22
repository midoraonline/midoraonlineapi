from fastapi import HTTPException
import pytest

from shop.publish_gates import (
    MIN_PUBLISH_PHOTOS,
    assert_media_limits,
    assert_price_for_publish,
    location_is_usable,
)


def test_media_min_photos_when_publishing():
    with pytest.raises(HTTPException) as ei:
        assert_media_limits(["https://x/a.jpg"], publishing=True)
    assert ei.value.detail["code"] == "photos_required"


def test_media_allows_draft_with_one_photo():
    assert_media_limits(["https://x/a.jpg"], publishing=False)


def test_media_max_eight():
    urls = [f"https://x/{i}.jpg" for i in range(9)]
    with pytest.raises(HTTPException) as ei:
        assert_media_limits(urls, publishing=False)
    assert ei.value.detail["code"] == "too_many_media"


def test_price_required_for_product():
    with pytest.raises(HTTPException) as ei:
        assert_price_for_publish(0, "product")
    assert ei.value.detail["code"] == "price_required"


def test_quote_ok_for_service():
    assert_price_for_publish(0, "service")


def test_location_rejects_uganda_only():
    assert location_is_usable(None, "Uganda") is False
    assert location_is_usable("Kampala", None) is True
    assert MIN_PUBLISH_PHOTOS == 2
