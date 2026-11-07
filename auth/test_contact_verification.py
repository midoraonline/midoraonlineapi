"""Contact OTP rules: E.164 phones, hashed codes, and posting eligibility."""

from datetime import datetime, timedelta, timezone

import pytest

from auth.contact import contact_verification_status
from auth.phone import PhoneError, normalize_phone
from common.verification_service import (
    VerificationError,
    confirm_verification_code,
    issue_verification_code,
    send_verification_code,
)
from notifications.africastalking import _sms_url


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store: list):
        self.store = store
        self.filters: list[tuple] = []
        self.op = "select"
        self.payload = None
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def is_(self, key, value):
        self.filters.append(("is", key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def _match(self, row: dict) -> bool:
        for op, key, value in self.filters:
            if op == "eq" and row.get(key) != value:
                return False
            if op == "is" and value is None and row.get(key) is not None:
                return False
            if op == "gte" and str(row.get(key) or "") < str(value):
                return False
        return True

    def execute(self):
        if self.op == "insert":
            row = {
                "id": f"code-{len(self.store) + 1}",
                "attempts": 0,
                "verified_at": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **self.payload,
            }
            self.store.append(row)
            return _Resp([row])
        rows = sorted(
            (row for row in self.store if self._match(row)),
            key=lambda row: row.get("created_at") or "",
            reverse=True,
        )
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        if self.op == "update":
            for row in rows:
                row.update(self.payload)
            return _Resp(rows)
        if self.op == "delete":
            for row in list(rows):
                self.store.remove(row)
            return _Resp(rows)
        return _Resp(rows)


class _Admin:
    def __init__(self):
        self.store: list = []

    def table(self, _name):
        return _Query(self.store)


@pytest.fixture
def codes(monkeypatch):
    admin = _Admin()
    monkeypatch.setattr("common.verification_service.get_supabase_admin", lambda: admin)
    return admin


def test_normalize_phone_defaults_to_uganda():
    assert normalize_phone("0700 123 456") == "+256700123456"
    assert normalize_phone("+256700123456") == "+256700123456"
    assert normalize_phone("256700123456") == "+256700123456"
    assert normalize_phone("700123456") == "+256700123456"
    with pytest.raises(PhoneError) as exc:
        normalize_phone("12")
    assert exc.value.code == "invalid_phone"


def test_posting_requires_both_channels_verified():
    strict = {"require_phone": True, "require_email": True}
    # Arrange / Act
    nothing = contact_verification_status(
        {"email": "a@midora.app", "email_verified": False, "phone_number": "0700", "phone_verified": False}, **strict
    )
    email_only = contact_verification_status(
        {"email": "a@midora.app", "email_verified": True, "phone_number": None, "phone_verified": False}, **strict
    )
    phone_only = contact_verification_status(
        {"email": "a@midora.app", "email_verified": False, "phone_number": "0700", "phone_verified": True}, **strict
    )
    both = contact_verification_status(
        {"email": "a@midora.app", "email_verified": True, "phone_number": "0700", "phone_verified": True}, **strict
    )
    # Assert
    assert (nothing["can_post"], nothing["required_channel"]) == (False, "phone")
    assert (email_only["can_post"], email_only["required_channel"]) == (False, "phone")
    assert (phone_only["can_post"], phone_only["required_channel"]) == (False, "email")
    assert (both["can_post"], both["required_channel"]) == (True, None)


def test_switches_off_let_unverified_sellers_post():
    row = {"email": "a@midora.app", "email_verified": False, "phone_number": None, "phone_verified": False}
    off = contact_verification_status(row, require_phone=False, require_email=False)
    email_only = contact_verification_status(row, require_phone=False, require_email=True)
    assert (off["can_post"], off["required_channel"]) == (True, None)
    assert (email_only["can_post"], email_only["required_channel"]) == (False, "email")


def test_gate_is_skipped_when_switches_off(monkeypatch):
    from shop.publish_gates import assert_contact_verified, assert_owner_phone_for_whatsapp

    monkeypatch.setattr("platform_settings.flags.phone_verification_required", lambda: False)
    monkeypatch.setattr("platform_settings.flags.email_verification_required", lambda: False)

    def _no_db():
        raise AssertionError("database should not be read")

    monkeypatch.setattr("db.supabase.get_supabase_admin", _no_db)
    assert_contact_verified("user-1")
    assert assert_owner_phone_for_whatsapp(object(), "owner-1") is True



def test_code_is_stored_hashed_and_confirm_updates(codes):
    code = issue_verification_code(
        user_id="user-1",
        contact="+256700123456",
        purpose="phone",
        channel="sms",
        target_type="user",
        target_id="user-1",
    )
    assert codes.store[0]["code_hash"] != code
    assert "code" not in codes.store[0]
    confirmed = confirm_verification_code(
        user_id="user-1",
        code=code,
        purpose="phone",
        target_type="user",
        target_id="user-1",
        contact="+256700123456",
    )
    assert confirmed == "+256700123456"
    assert codes.store[0]["verified_at"]


def test_wrong_code_locks_after_five_attempts(codes):
    issue_verification_code(
        user_id="user-1",
        contact="+256700123456",
        purpose="phone",
        channel="sms",
        target_type="user",
        target_id="user-1",
    )
    for _ in range(4):
        with pytest.raises(VerificationError) as exc:
            confirm_verification_code(
                user_id="user-1",
                code="000000",
                purpose="phone",
                target_type="user",
                target_id="user-1",
            )
        assert exc.value.code == "code_invalid"
    with pytest.raises(VerificationError) as exc:
        confirm_verification_code(
            user_id="user-1",
            code="000000",
            purpose="phone",
            target_type="user",
            target_id="user-1",
        )
    assert exc.value.code == "too_many_attempts"


def test_expired_code(codes):
    issue_verification_code(
        user_id="user-1",
        contact="a@midora.app",
        purpose="email",
        channel="email",
        target_type="user",
        target_id="user-1",
    )
    codes.store[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(VerificationError) as exc:
        confirm_verification_code(
            user_id="user-1",
            code="123456",
            purpose="email",
            target_type="user",
            target_id="user-1",
            contact="a@midora.app",
        )
    assert exc.value.code == "code_expired"


def test_resend_cooldown_and_hourly_cap(codes):
    issue_verification_code(
        user_id="user-1",
        contact="+256700123456",
        purpose="phone",
        channel="sms",
        target_type="user",
        target_id="user-1",
    )
    with pytest.raises(VerificationError) as exc:
        issue_verification_code(
            user_id="user-1",
            contact="+256700123456",
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id="user-1",
        )
    assert exc.value.code == "resend_too_soon"

    older = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    for row in codes.store:
        row["created_at"] = older
    for _ in range(4):
        issue_verification_code(
            user_id="user-1",
            contact="+256700123456",
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id="user-1",
        )
        for row in codes.store:
            row["created_at"] = older
    with pytest.raises(VerificationError) as exc:
        issue_verification_code(
            user_id="user-1",
            contact="+256700123456",
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id="user-1",
        )
    assert exc.value.code == "resend_too_soon"
    assert len(codes.store) == 5


def test_sms_failure_does_not_keep_the_code(codes, monkeypatch):
    monkeypatch.setattr("notifications.africastalking.send_sms", lambda *_a, **_k: False)
    with pytest.raises(VerificationError) as exc:
        send_verification_code(
            user_id="user-1",
            phone_number="+256700123456",
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id="user-1",
        )
    assert exc.value.code == "sms_unavailable"
    assert codes.store == []


def test_unverified_seller_is_blocked_from_posting(monkeypatch):
    from fastapi import HTTPException

    from shop.publish_gates import assert_contact_verified

    monkeypatch.setattr("platform_settings.flags.phone_verification_required", lambda: True)
    monkeypatch.setattr("platform_settings.flags.email_verification_required", lambda: True)

    class _Users:
        def __init__(self, row):
            self.row = row

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return _Resp([self.row])

    class _UsersAdmin:
        def __init__(self, row):
            self.row = row

        def table(self, _name):
            return _Users(self.row)

    monkeypatch.setattr(
        "db.supabase.get_supabase_admin",
        lambda: _UsersAdmin(
            {
                "email": "a@midora.app",
                "email_verified": False,
                "phone_number": None,
                "phone_verified": False,
            }
        ),
    )
    with pytest.raises(HTTPException) as exc:
        assert_contact_verified("user-1")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "verification_required"

    monkeypatch.setattr(
        "db.supabase.get_supabase_admin",
        lambda: _UsersAdmin(
            {
                "email": "a@midora.app",
                "email_verified": True,
                "phone_number": None,
                "phone_verified": False,
            }
        ),
    )
    with pytest.raises(HTTPException):
        assert_contact_verified("user-1")

    monkeypatch.setattr(
        "db.supabase.get_supabase_admin",
        lambda: _UsersAdmin(
            {
                "email": "a@midora.app",
                "email_verified": True,
                "phone_number": "+256700123456",
                "phone_verified": True,
            }
        ),
    )
    assert_contact_verified("user-1")


def test_sandbox_sms_host():
    assert _sms_url("sandbox") == "https://api.sandbox.africastalking.com/version1/messaging/bulk"
    assert _sms_url("midora") == "https://api.africastalking.com/version1/messaging/bulk"
