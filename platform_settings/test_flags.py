from platform_settings import flags
from platform_settings.flags import SignupsClosed, assert_signups_open


def test_defaults_when_table_is_empty(monkeypatch):
    flags.invalidate_cache()
    monkeypatch.setattr(flags, "_load_rows", lambda: [])
    assert flags.analytics_enabled() is False
    assert flags.signups_allowed() is True
    assert flags.listings_require_review() is False
    assert flags.ai_moderation_enabled() is True
    assert flags.enabled("maintenance_mode") is False
    public = flags.public_flags()
    assert public == {
        "analytics": False,
        "signups_allowed": True,
        "listings_require_review": False,
        "ai_moderation": True,
        "maintenance_mode": False,
    }
    assert all(isinstance(value, bool) for value in public.values())


def test_stored_flag_overrides_default(monkeypatch):
    flags.invalidate_cache()
    monkeypatch.setattr(
        flags,
        "_load_rows",
        lambda: [{"key": "analytics", "enabled": True, "value": {"secret": "nope"}, "updated_by": None, "updated_at": None}],
    )
    assert flags.analytics_enabled() is True
    assert "secret" not in flags.public_flags()


def test_signups_closed(monkeypatch):
    monkeypatch.setattr(flags, "signups_allowed", lambda: False)
    try:
        assert_signups_open()
    except SignupsClosed:
        return
    raise AssertionError("expected SignupsClosed")
