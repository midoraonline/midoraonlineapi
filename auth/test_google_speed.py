"""Guards for the Google sign-in latency work: no forced consent, one lookup, no duplicate status query."""
from urllib.parse import parse_qs, urlparse

import pytest

from auth import service as auth_service
from auth.providers import googleauth


class _Query:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def select(self, *_):
        return self

    def or_(self, expr):
        self.calls.append(expr)
        return self

    def limit(self, *_):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Client:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def table(self, _):
        return _Query(self.rows, self.calls)


def test_auth_url_does_not_force_consent(monkeypatch):
    monkeypatch.setattr(googleauth, "_get_google_oauth_settings", lambda: ("cid", "sec", "https://x/cb"))
    params = parse_qs(urlparse(googleauth.get_redirect_url(state="s")).query)
    assert "prompt" not in params and "access_type" not in params
    assert params["scope"] == ["openid email profile"]


def test_single_lookup_prefers_sub_match(monkeypatch):
    rows = [
        {"id": "email-row", "google_sub": None},
        {"id": "sub-row", "google_sub": "SUB"},
    ]
    client = _Client(rows)
    monkeypatch.setattr(googleauth, "get_supabase_admin", lambda: client)
    user = googleauth._find_google_user("SUB", "a@b.com")
    assert user["id"] == "sub-row"
    assert len(client.calls) == 1


def test_lookup_quotes_hostile_values(monkeypatch):
    client = _Client([])
    monkeypatch.setattr(googleauth, "get_supabase_admin", lambda: client)
    googleauth._find_google_user('s",id.neq.0', 'a",x@b.com')
    expr = client.calls[0]
    assert '\\"' in expr and 'id.neq.0"' in expr  # stays inside the quoted value


def test_status_helper_matches_account_check():
    auth_service.assert_status_active("active")
    auth_service.assert_status_active(None)
    with pytest.raises(auth_service.AccountInactive):
        auth_service.assert_status_active("suspended")
