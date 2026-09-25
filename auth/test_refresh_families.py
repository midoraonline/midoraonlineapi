"""Refresh-token families: reuse stays inside one login."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from auth.routes import google as google_routes
from auth.routes import session as session_routes
from auth.service import (
    create_access_and_refresh_tokens,
    decode_token,
    rotate_refresh_token,
)


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self._filters = []
        self._op = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def eq(self, key, value):
        self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def is_(self, key, value):
        if value is None:
            self._filters.append(lambda row, key=key: row.get(key) is None)
        else:
            self._filters.append(lambda row, key=key, value=value: row.get(key) == value)
        return self

    def limit(self, _n):
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
            row = dict(self._payload)
            rows.append(row)
            return SimpleNamespace(data=[row])
        matched = [row for row in rows if all(check(row) for check in self._filters)]
        if self._op == "update":
            for row in matched:
                row.update(self._payload)
            return SimpleNamespace(data=list(matched))
        return SimpleNamespace(data=list(matched))


class _Client:
    def __init__(self):
        self.db = {"users": [{"id": "user-1", "user_role": "customer"}]}

    def table(self, name):
        return _Query(self.db, name)


def _bind(monkeypatch) -> _Client:
    client = _Client()
    monkeypatch.setattr("auth.service.get_supabase_admin", lambda: client)
    monkeypatch.setattr("auth.providers.emailpassword.get_supabase_admin", lambda: client)
    return client


def _jti(token: str) -> str:
    return str(decode_token(token)["jti"])


def _record(client: _Client, token: str) -> dict:
    jti = _jti(token)
    return next(row for row in client.db["refresh_tokens"] if row["jti"] == jti)


def test_rotation_keeps_family_and_replaces_the_presented_token(monkeypatch):
    client = _bind(monkeypatch)
    _, refresh = create_access_and_refresh_tokens("user-1", "customer")
    family_id = _record(client, refresh)["family_id"]

    _, rotated, _ = rotate_refresh_token(refresh)

    assert _record(client, refresh)["revoked_at"]
    assert _record(client, rotated)["family_id"] == family_id
    assert not _record(client, rotated).get("revoked_at")

    _, again, _ = rotate_refresh_token(rotated)
    assert _record(client, again)["family_id"] == family_id
    assert not _record(client, again).get("revoked_at")


def test_reuse_within_a_family_revokes_that_family(monkeypatch):
    client = _bind(monkeypatch)
    _, refresh = create_access_and_refresh_tokens("user-1", "customer")
    family_id = _record(client, refresh)["family_id"]
    _, current, _ = rotate_refresh_token(refresh)

    with pytest.raises(ValueError):
        rotate_refresh_token(refresh)

    family_rows = [row for row in client.db["refresh_tokens"] if row["family_id"] == family_id]
    assert family_rows
    assert all(row.get("revoked_at") for row in family_rows)
    with pytest.raises(ValueError):
        rotate_refresh_token(current)


def test_reused_revoked_token_does_not_revoke_a_newer_login(monkeypatch):
    client = _bind(monkeypatch)
    _, old_refresh = create_access_and_refresh_tokens("user-1", "customer")
    old_family = _record(client, old_refresh)["family_id"]
    _, old_current, _ = rotate_refresh_token(old_refresh)

    _, new_refresh = create_access_and_refresh_tokens("user-1", "customer")
    new_family = _record(client, new_refresh)["family_id"]
    assert new_family != old_family

    with pytest.raises(ValueError):
        rotate_refresh_token(old_refresh)

    assert _record(client, old_current).get("revoked_at")
    assert not _record(client, new_refresh).get("revoked_at")

    _, newer, _ = rotate_refresh_token(new_refresh)
    assert _record(client, newer)["family_id"] == new_family
    assert not _record(client, newer).get("revoked_at")


def _refresh_client(monkeypatch) -> tuple[TestClient, _Client]:
    db = _bind(monkeypatch)
    app = FastAPI()
    app.include_router(session_routes.router, prefix="/api/v1/auth")
    return TestClient(app), db


def _cookie_cleared(response) -> bool:
    header = " ".join(response.headers.get_list("set-cookie")).lower()
    return "midora_refresh=" in header and "max-age=0" in header


def test_revoked_refresh_returns_401_and_clears_cookie(monkeypatch):
    api, client = _refresh_client(monkeypatch)
    _, refresh = create_access_and_refresh_tokens("user-1", "customer")
    _, current, _ = rotate_refresh_token(refresh)
    _, other_login = create_access_and_refresh_tokens("user-1", "customer")

    response = api.post("/api/v1/auth/refresh", cookies={"midora_refresh": refresh})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid refresh token", "code": "invalid_refresh"}
    assert _cookie_cleared(response)
    assert not _record(client, other_login).get("revoked_at")
    assert _record(client, current).get("revoked_at")


def test_unknown_refresh_returns_401_and_clears_cookie(monkeypatch):
    api, client = _refresh_client(monkeypatch)
    _, kept = create_access_and_refresh_tokens("user-1", "customer")

    response = api.post("/api/v1/auth/refresh", cookies={"midora_refresh": "not-a-token"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh"
    assert _cookie_cleared(response)
    assert not _record(client, kept).get("revoked_at")


def test_google_callback_does_not_put_tokens_in_the_redirect(monkeypatch):
    monkeypatch.setattr(
        google_routes,
        "get_settings",
        lambda: SimpleNamespace(
            google_oauth_frontend_callback_url="https://shop.example/auth/google/callback"
        ),
    )
    monkeypatch.setattr(
        google_routes,
        "handle_callback",
        lambda **_kwargs: {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "user": {"id": "user-1"},
        },
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/v1/auth/google/callback",
        "raw_path": b"/api/v1/auth/google/callback",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
        "scheme": "http",
    }
    response = asyncio.run(
        google_routes.google_oauth_callback(Request(scope), code="auth-code", state=None)
    )
    location = response.headers["location"]
    assert "access-secret" not in location
    assert "refresh-secret" not in location
    assert "access_token" not in location
    assert "refresh_token" not in location
    assert "verified=true" in location
    assert "provider=google" in location
