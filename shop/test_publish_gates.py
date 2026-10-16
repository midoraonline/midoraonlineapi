from fastapi import HTTPException
import pytest

from shop.publish_gates import (
    MIN_PUBLISH_PHOTOS,
    assert_media_limits,
    assert_price_for_publish,
    location_is_usable,
    photos_required_for_item_type,
)


def test_media_min_photos_when_publishing():
    with pytest.raises(HTTPException) as ei:
        assert_media_limits(["https://x/a.jpg"], publishing=True)
    assert ei.value.detail["code"] == "photos_required"


def test_media_allows_draft_with_one_photo():
    assert_media_limits(["https://x/a.jpg"], publishing=False)


def test_media_max_three():
    urls = [f"https://x/{i}.jpg" for i in range(4)]
    with pytest.raises(HTTPException) as ei:
        assert_media_limits(urls, publishing=False)
    assert ei.value.detail["code"] == "too_many_photos"


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


def test_media_optional_for_opportunity_and_service():
    assert_media_limits([], publishing=True, item_type="opportunity")
    assert_media_limits([], publishing=True, item_type="service")
    assert_media_limits([], publishing=True, item_type="job")
    assert photos_required_for_item_type("product") is True
    assert photos_required_for_item_type("opportunity") is False


def test_media_still_required_for_product():
    with pytest.raises(HTTPException) as ei:
        assert_media_limits([], publishing=True, item_type="product")
    assert ei.value.detail["code"] == "photos_required"


def test_media_limits_follow_admin_setting(monkeypatch):
    import platform_settings.flags as flags

    monkeypatch.setattr(flags, "media_limits", lambda plan_tier=None: {"max_photos": 5, "max_videos": 0})
    assert_media_limits([f"https://x/{i}.jpg" for i in range(5)], publishing=False)
    with pytest.raises(HTTPException) as ei:
        assert_media_limits([f"https://x/{i}.jpg" for i in range(6)], publishing=False)
    assert ei.value.detail["code"] == "too_many_photos"
    with pytest.raises(HTTPException) as ei:
        assert_media_limits(["https://x/a.jpg", "https://x/v.mp4"], publishing=False)
    assert ei.value.detail["code"] == "too_many_videos"


def test_media_limits_value_validation():
    from platform_settings.flags import normalize_media_limits_value

    with pytest.raises(HTTPException):
        normalize_media_limits_value({"max_photos": 0})
    with pytest.raises(HTTPException):
        normalize_media_limits_value({"max_videos": 99})


def test_media_limits_per_plan(monkeypatch):
    import payments.plans as plans
    import platform_settings.flags as flags

    monkeypatch.setattr(plans, "get_plan", lambda tier: {})

    monkeypatch.setattr(
        flags,
        "load_settings",
        lambda **kw: {
            "listing_media_limits": {
                "value": {
                    "default": {"max_photos": 3, "max_videos": 1},
                    "plans": {"premium": {"max_photos": 10, "max_videos": 2}},
                }
            }
        },
    )
    assert flags.media_limits("premium") == {"max_photos": 10, "max_videos": 2}
    assert flags.media_limits("basic") == {"max_photos": 3, "max_videos": 1}
    assert flags.media_limits(None) == {"max_photos": 3, "max_videos": 1}
    assert_media_limits([f"https://x/{i}.jpg" for i in range(10)], publishing=False, plan_tier="premium")
    with pytest.raises(HTTPException):
        assert_media_limits([f"https://x/{i}.jpg" for i in range(4)], publishing=False, plan_tier="basic")


def test_legacy_flat_value_is_default(monkeypatch):
    import payments.plans as plans
    import platform_settings.flags as flags

    monkeypatch.setattr(plans, "get_plan", lambda tier: {})

    monkeypatch.setattr(flags, "load_settings", lambda **kw: {"listing_media_limits": {"value": {"max_photos": 5, "max_videos": 0}}})
    assert flags.media_limits("standard") == {"max_photos": 5, "max_videos": 0}


def test_normalize_plan_value(monkeypatch):
    import platform_settings.flags as flags

    monkeypatch.setattr(flags, "load_settings", lambda **kw: {})
    out = flags.normalize_media_limits_value({"plans": {"standard": {"max_photos": 6}}})
    assert out["plans"]["standard"] == {"max_photos": 6, "max_videos": 1}
    assert out["default"] == {"max_photos": 3, "max_videos": 1}
    with pytest.raises(HTTPException):
        flags.normalize_media_limits_value({"plans": {"gold": {"max_photos": 6}}})


def test_plan_table_values_win(monkeypatch):
    import payments.plans as plans
    import platform_settings.flags as flags

    monkeypatch.setattr(flags, "load_settings", lambda **kw: {})
    monkeypatch.setattr(plans, "get_plan", lambda tier: {"max_photos": 8, "max_videos": 2} if tier == "premium" else {})
    assert flags.media_limits("premium") == {"max_photos": 8, "max_videos": 2}
    assert flags.media_limits("basic") == {"max_photos": 3, "max_videos": 1}
