import asyncio

import pytest


def test_create_shop_requires_verified_account_contact(monkeypatch):
    from fastapi import HTTPException, Request, Response

    from tenants.router import create_shop
    from tenants.schemas import ShopCreate

    checked_users = []

    def require_verified_contact(user_id):
        checked_users.append(user_id)
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "Verify your phone or email before posting a listing.",
                "code": "verification_required",
            },
        )

    monkeypatch.setattr("platform_settings.flags.assert_posting_open", lambda: None)
    monkeypatch.setattr("shop.publish_gates.assert_contact_verified", require_verified_contact)

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/shops",
            "raw_path": b"/shops",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            create_shop(
                body=ShopCreate(name="Test shop", slug="test-shop"),
                client=object(),
                request=request,
                response=Response(),
                user_id="user-1",
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "verification_required"
    assert checked_users == ["user-1"]