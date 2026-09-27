"""Password check skips bcrypt for Google-only accounts. id_token avoids userinfo."""

import time

import jwt

from auth.providers.googleauth import _profile_from_id_token
from auth.service import UNUSABLE_PASSWORD_HASH, hash_password, verify_password


def test_unusable_password_does_not_run_bcrypt():
    hashed = hash_password("correct-horse")
    started = time.perf_counter()
    assert verify_password("nope", UNUSABLE_PASSWORD_HASH) is False
    fast = time.perf_counter() - started
    started = time.perf_counter()
    assert verify_password("correct-horse", hashed) is True
    slow = time.perf_counter() - started
    assert fast < slow
    assert fast < 0.01


def test_id_token_profile_checks_audience_and_email():
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": "client-id",
            "sub": "google-sub",
            "email": "ada@example.com",
            "name": "Ada",
            "exp": now + 60,
        },
        "unused",
        algorithm="HS256",
    )
    profile = _profile_from_id_token(token, "client-id")
    assert profile["email"] == "ada@example.com"
    assert profile["sub"] == "google-sub"
    assert _profile_from_id_token(token, "other-client") is None
