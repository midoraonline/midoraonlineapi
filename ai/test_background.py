import asyncio

import pytest

from ai import background


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(background, "get_settings", lambda: type("S", (), {"remove_bg_api_key": ""})())
    with pytest.raises(background.BackgroundRemovalUnavailable):
        asyncio.run(background.remove_background_to_url("https://x/a.jpg", user_id="u"))


def test_rejects_non_https(monkeypatch):
    monkeypatch.setattr(background, "get_settings", lambda: type("S", (), {"remove_bg_api_key": "k"})())
    with pytest.raises(background.BackgroundRemovalFailed):
        asyncio.run(background.remove_background_to_url("http://169.254.169.254/x", user_id="u"))


def test_normalize_color():
    assert background.normalize_color("#FFFFFF") == "ffffff"
    assert background.normalize_color("red") is None
    assert background.normalize_color(None) is None
