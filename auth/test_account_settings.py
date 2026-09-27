"""Account settings: preferences, bio, and avatar bytes that are not recompressed."""

import io

import pytest
from PIL import Image
from pydantic import ValidationError

from auth.preferences import (
    UserPreferencesPatch,
    coerce_preferences,
    merge_preferences,
    notification_enabled,
)
from auth.schemas import UpdateProfileRequest, profile_response
from media.avatar_image import is_heic, prepare_avatar_upload


def test_preferences_round_trip_and_partial_patch():
    stored = coerce_preferences({"theme": "dark", "notifications": {"push": False}})
    assert stored["theme"] == "dark"
    assert stored["notifications"]["push"] is False
    assert stored["notifications"]["email"] is True
    assert stored["notifications"]["reports"] is True

    merged = merge_preferences(
        stored,
        UserPreferencesPatch.model_validate({"notifications": {"messages": False}}),
    )
    assert merged["theme"] == "dark"
    assert merged["notifications"]["push"] is False
    assert merged["notifications"]["messages"] is False

    with pytest.raises(ValidationError):
        UpdateProfileRequest.model_validate({"preferences": {"theme": "blue"}})


def test_profile_payload_includes_theme_for_session():
    body = profile_response(
        {
            "id": "u1",
            "email": "ada@example.com",
            "email_verified": True,
            "full_name": "Ada",
            "phone_number": "+256700000000",
            "phone_verified": False,
            "bio": "Kampala tailor",
            "user_role": "admin",
            "avatar_url": "https://utfs.io/f/avatar",
            "preferences": {"theme": "light"},
        },
        realtime_token="realtime",
    )
    dumped = body.model_dump()
    assert dumped["bio"] == "Kampala tailor"
    assert dumped["preferences"]["theme"] == "light"
    assert dumped["preferences"]["notifications"]["listing_approved"] is True


def test_notification_toggles(monkeypatch):
    monkeypatch.setattr(
        "auth.preferences.load_preferences",
        lambda _user: coerce_preferences(
            {"notifications": {"push": False, "email": True, "messages": False, "reports": False}}
        ),
    )
    assert notification_enabled("u", "listing_approved", "push") is False
    assert notification_enabled("u", "listing_approved", "email") is True
    assert notification_enabled("u", "messages", "email") is False
    assert notification_enabled("u", "reports", "in-app") is False


def test_non_heic_avatar_bytes_are_not_reencoded():
    image = Image.new("RGB", (6, 4), (1, 2, 3))
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    original = raw.getvalue()
    out, content_type, name = prepare_avatar_upload(
        original,
        content_type="image/png",
        filename="me.png",
    )
    assert out == original
    assert content_type == "image/png"
    assert name == "me.png"


def test_heic_converts_at_full_resolution():
    import pillow_heif

    image = Image.new("RGB", (12, 9), (10, 20, 30))
    heif = io.BytesIO()
    pillow_heif.from_pillow(image).save(heif)
    data = heif.getvalue()
    assert is_heic(data, "application/octet-stream", "photo.heic")
    out, content_type, name = prepare_avatar_upload(
        data,
        content_type="image/heic",
        filename="photo.heic",
    )
    assert content_type == "image/jpeg"
    assert name == "photo.jpg"
    with Image.open(io.BytesIO(out)) as jpeg:
        assert jpeg.size == (12, 9)
        assert jpeg.format == "JPEG"
