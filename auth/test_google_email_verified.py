import asyncio

import pytest

from auth.providers.googleauth import google_email_verified
from auth.routes import google as google_routes


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), ("true", True), ("True", True), (False, False), ("false", False), (None, False)],
)
def test_google_email_verified_claim(value, expected):
    assert google_email_verified({"email_verified": value}) is expected


def test_unverified_google_account_gets_link(monkeypatch):
    sent: list[tuple] = []

    async def fake_send(user_id, email, *, confirm):
        sent.append((user_id, email, confirm))

    monkeypatch.setattr("auth.routes.register.send_new_verification_link", fake_send)
    user = {"id": "u1", "email": "a@b.com"}
    asyncio.run(google_routes._verify_if_needed({"user": user, "needs_email_verification": True}))
    asyncio.run(google_routes._verify_if_needed({"user": user, "needs_email_verification": False}))
    assert sent == [("u1", "a@b.com", False)]
