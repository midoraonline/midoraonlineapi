"""Shop WhatsApp numbers: normalised for every country, checked via the worker, hidden unless on WhatsApp."""
import pathlib
import re
from types import SimpleNamespace

import pytest

from shop.routes import whatsapp as whatsapp_routes
from common.whatsapp_number import WhatsAppNumberError, link_digits, normalize_whatsapp
from shop import whatsapp_check
from tenants import service as tenants_service
from tenants.schemas import ShopUpdate

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "raw,region,expected",
    [
        ("0700123456", None, "+256700123456"),  # Uganda is the default
        ("+256 0700 123 456", None, "+256700123456"),  # pasted trunk zero
        ("256700123456", None, "+256700123456"),
        ("0712345678", "KE", "+254712345678"),
        ("00254712345678", None, "+254712345678"),
        ("2348031234567", None, "+2348031234567"),  # country code without +
        ("+1 415 555 2671", "UG", "+14155552671"),
    ],
)
def test_normalises_any_country(raw, region, expected):
    assert normalize_whatsapp(raw, region) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12", "0000000000"])
def test_rejects_junk(raw):
    with pytest.raises(WhatsAppNumberError):
        normalize_whatsapp(raw)


def test_link_digits_has_no_plus():
    assert link_digits("+256700123456") == "256700123456"


# ---- lookup order: cache, then worker, unknown never recorded -------------------------------

def test_cache_hit_skips_worker(monkeypatch):
    monkeypatch.setattr(whatsapp_check, "cached_result", lambda e164: True)
    monkeypatch.setattr(whatsapp_check, "ask_worker", lambda e164: pytest.fail("worker must not be called"))
    assert whatsapp_check.lookup("+256700123456") == (True, "cache")


def test_live_answer_is_cached(monkeypatch):
    stored = []
    monkeypatch.setattr(whatsapp_check, "cached_result", lambda e164: None)
    monkeypatch.setattr(whatsapp_check, "ask_worker", lambda e164: False)
    monkeypatch.setattr(whatsapp_check, "_store", lambda *a: stored.append(a))
    assert whatsapp_check.lookup("+256700123456") == (False, "library")
    assert stored == [("+256700123456", False, "library")]


def test_worker_down_is_unknown_and_not_cached(monkeypatch):
    monkeypatch.setattr(whatsapp_check, "cached_result", lambda e164: None)
    monkeypatch.setattr(whatsapp_check, "ask_worker", lambda e164: None)
    monkeypatch.setattr(whatsapp_check, "_store", lambda *a: pytest.fail("unknown must not be cached"))
    assert whatsapp_check.lookup("+256700123456") == (None, "unavailable")


def test_unknown_leaves_shop_untouched(monkeypatch):
    monkeypatch.setattr(whatsapp_check, "get_supabase_admin", lambda: pytest.fail("no write for unknown"))
    whatsapp_check.record_on_shop("s1", None, "unavailable")


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, str(body)

    def json(self):
        return self._body


def _configure(monkeypatch, url="https://checker.example"):
    settings = SimpleNamespace(wa_checker_url=url, wa_checker_token="tok", wa_checker_timeout_seconds=1)
    monkeypatch.setattr(whatsapp_check, "get_settings", lambda: settings)


@pytest.mark.parametrize(
    "status,body,expected",
    [(200, {"registered": True}, True), (200, {"registered": False}, False),
     (429, {"error": "rate_limited"}, None), (503, {"error": "unavailable"}, None),
     (200, {"registered": "maybe"}, None)],
)
def test_worker_responses(monkeypatch, status, body, expected):
    _configure(monkeypatch)
    monkeypatch.setattr(whatsapp_check.httpx, "post", lambda *a, **k: _Resp(status, body))
    assert whatsapp_check.ask_worker("+256700123456") is expected


def test_worker_network_error_is_unknown(monkeypatch):
    _configure(monkeypatch)

    def boom(*a, **k):
        raise whatsapp_check.httpx.ConnectError("down")

    monkeypatch.setattr(whatsapp_check.httpx, "post", boom)
    assert whatsapp_check.ask_worker("+256700123456") is None


def test_disabled_without_config(monkeypatch):
    _configure(monkeypatch, url="")
    monkeypatch.setattr(whatsapp_check.httpx, "post", lambda *a, **k: pytest.fail("must not call"))
    assert whatsapp_check.ask_worker("+256700123456") is None


def test_worker_gets_bearer_token_and_e164(monkeypatch):
    _configure(monkeypatch)
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return _Resp(200, {"registered": True})

    monkeypatch.setattr(whatsapp_check.httpx, "post", fake_post)
    whatsapp_check.ask_worker("+256700123456")
    assert seen["url"] == "https://checker.example/check"
    assert seen["json"] == {"number": "+256700123456"}
    assert seen["headers"]["Authorization"] == "Bearer tok"


# ---- Africa's Talking WhatsApp is gone; only the library decides ------------------------------

def test_no_whatsapp_otp_left():
    from notifications import africastalking

    assert not hasattr(africastalking, "send_whatsapp_text")
    fields = get_settings_module().Settings.model_fields
    assert "africastalking_api_key" in fields  # SMS config stays; proves we are reading the right thing
    assert "africastalking_whatsapp_number" not in fields
    paths = {getattr(r, "path", "") for r in whatsapp_routes.router.routes}
    assert paths == {"/{shop_id}/whatsapp/check"}


def get_settings_module():
    from core import config

    return config


# ---- changing the number resets what we knew about the old one ---------------------------------

class _Table:
    def __init__(self, log):
        self.log = log

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def limit(self, *_):
        return self

    def update(self, payload):
        self.log.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=[{"whatsapp_number": "+256700000001"}])


def test_changing_number_resets_verification(monkeypatch):
    log = []
    client = SimpleNamespace(table=lambda name: _Table(log))
    monkeypatch.setattr(tenants_service, "get_shop", lambda *a, **k: {"id": "s"})
    monkeypatch.setattr(tenants_service, "revalidate_nextjs_cache_tag", lambda *a: None)
    tenants_service.update_shop(client, "s", ShopUpdate(whatsapp_number="0712345678", whatsapp_country="KE"))
    payload = log[0]
    assert payload["whatsapp_number"] == "+254712345678"
    assert payload["whatsapp_verified"] is False
    assert payload["whatsapp_registered"] is None
    assert "whatsapp_country" not in payload


def test_invalid_number_is_rejected_before_saving(monkeypatch):
    client = SimpleNamespace(table=lambda name: pytest.fail("must not touch the database"))
    with pytest.raises(WhatsAppNumberError):
        tenants_service.update_shop(client, "s", ShopUpdate(whatsapp_number="12"))


# ---- leak guard: public queries read the generated column, never the raw number ---------------

@pytest.mark.parametrize("path", ["shop/service.py", "search/service.py", "feed/composite.py"])
def test_public_selects_use_the_generated_column(path):
    text = (ROOT / path).read_text()
    assert "whatsapp_number:whatsapp_public" in text
    assert not re.search(r'(?<![:\w])whatsapp_number,', text.replace("whatsapp_number:whatsapp_public", "")), path
