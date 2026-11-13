"""Shop WhatsApp numbers: normalised for every country, verified by a Cloud API code, hidden until verified."""
import pathlib
import re
from types import SimpleNamespace

import pytest

from shop.routes import whatsapp as whatsapp_routes
from common.whatsapp_number import WhatsAppNumberError, link_digits, normalize_whatsapp
from notifications import whatsapp_cloud
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


# ---- extra shop phone numbers ------------------------------------------------------------------

def test_extra_contacts_are_normalised_and_typed():
    from common.whatsapp_number import clean_shop_contacts

    out = clean_shop_contacts([
        {"type": "phone", "label": " Branch ", "value": "0700123456"},
        {"type": "whatsapp", "value": "+254712345678"},
        {"type": "javascript", "value": "0701234567"},
        {"value": "   "},
    ])
    assert out == [
        {"type": "phone", "label": "Branch", "value": "+256700123456"},
        {"type": "whatsapp", "label": None, "value": "+254712345678"},
        {"type": "phone", "label": None, "value": "+256701234567"},
    ]


@pytest.mark.parametrize(
    "contacts",
    [
        [{"value": n} for n in ("0700123456", "0701234567", "0702234567", "0703234567")],
        [{"type": "whatsapp", "value": "0700123456"}, {"type": "whatsapp", "value": "0701234567"}],
        [{"value": "0700123456"}, {"value": "+256700123456"}],
        [{"value": "12"}],
    ],
)
def test_extra_contacts_rules(contacts):
    from common.whatsapp_number import clean_shop_contacts

    with pytest.raises(WhatsAppNumberError):
        clean_shop_contacts(contacts)


def test_shop_by_slug_hides_unverified_numbers_from_buyers(monkeypatch):
    row = {
        "id": "s", "owner_id": "o", "whatsapp_number": "+256700123456", "whatsapp_public": None,
        "contacts": [{"type": "phone", "value": "+256701234567"}],
    }
    client = SimpleNamespace(table=lambda name: _Table([], data=[row]))
    monkeypatch.setattr(tenants_service.engagement_service, "get_shop_engagement", lambda *a, **k: {})
    monkeypatch.setattr("shop.publish_gates.assert_owner_phone_for_whatsapp", lambda *a: False)
    out = tenants_service.get_shop_by_slug(client, "s", viewer_id=None)
    assert out["whatsapp_number"] is None and out["contacts"] == []
    owner = tenants_service.get_shop_by_slug(client, "s", viewer_id="o")
    assert owner["whatsapp_number"] == "+256700123456" and owner["contacts"]


# ---- Cloud API sender -------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, str(body)
        self.content = b"x"

    def json(self):
        return self._body


def _configure(monkeypatch, token="tok"):
    settings = SimpleNamespace(
        whatsapp_api_url="https://graph.facebook.com",
        whatsapp_api_version="v21.0",
        whatsapp_api_token=token,
        whatsapp_phone_number_id="123",
        whatsapp_message_template="midorawhatsapp",
        whatsapp_template_language="en_US",
        whatsapp_timeout_seconds=1,
    )
    monkeypatch.setattr(whatsapp_cloud, "get_settings", lambda: settings)


def test_otp_is_sent_as_authentication_template(monkeypatch):
    _configure(monkeypatch)
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return _Resp(200, {"messages": [{"id": "wamid.ABC"}]})

    monkeypatch.setattr(whatsapp_cloud.httpx, "post", fake_post)
    assert whatsapp_cloud.send_otp("+256700123456", "482913") == "wamid.ABC"
    assert seen["url"] == "https://graph.facebook.com/v21.0/123/messages"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    body = seen["json"]
    assert body["to"] == "256700123456"
    assert body["template"]["name"] == "midorawhatsapp"
    body_param, button = body["template"]["components"]
    assert body_param["parameters"][0]["text"] == "482913"
    assert button["sub_type"] == "url" and button["parameters"][0]["text"] == "482913"


@pytest.mark.parametrize("status,body", [(400, {"error": {"code": 132001}}), (401, {}), (200, {"messages": []})])
def test_rejected_send_returns_none(monkeypatch, status, body):
    _configure(monkeypatch)
    monkeypatch.setattr(whatsapp_cloud.httpx, "post", lambda *a, **k: _Resp(status, body))
    assert whatsapp_cloud.send_otp("+256700123456", "111111") is None


def test_network_error_returns_none(monkeypatch):
    _configure(monkeypatch)

    def boom(*a, **k):
        raise whatsapp_cloud.httpx.ConnectError("down")

    monkeypatch.setattr(whatsapp_cloud.httpx, "post", boom)
    assert whatsapp_cloud.send_otp("+256700123456", "111111") is None


def test_unconfigured_never_calls_meta(monkeypatch):
    _configure(monkeypatch, token="")
    monkeypatch.setattr(whatsapp_cloud.httpx, "post", lambda *a, **k: pytest.fail("must not call"))
    assert whatsapp_cloud.send_otp("+256700123456", "111111") is None


def _status_settings(monkeypatch):
    _configure(monkeypatch)
    whatsapp_cloud.get_settings().whatsapp_business_account_id = "waba"


def _graph(phone_resp, template_resp):
    def fake_get(url, params, headers, timeout):
        return template_resp if url.endswith("/message_templates") else phone_resp
    return fake_get


def test_status_ready_when_template_approved(monkeypatch):
    _status_settings(monkeypatch)
    monkeypatch.setattr(whatsapp_cloud.httpx, "get", _graph(
        _Resp(200, {"display_phone_number": "+256 792", "verified_name": "Midora"}),
        _Resp(200, {"data": [{"name": "midorawhatsapp", "status": "APPROVED", "category": "AUTHENTICATION"}]}),
    ))
    assert whatsapp_cloud.connection_status()["ready"] is True


def test_status_explains_pending_template(monkeypatch):
    _status_settings(monkeypatch)
    monkeypatch.setattr(whatsapp_cloud.httpx, "get", _graph(
        _Resp(200, {"display_phone_number": "+256 792"}),
        _Resp(200, {"data": [{"name": "midorawhatsapp", "status": "PENDING", "category": "AUTHENTICATION"}]}),
    ))
    out = whatsapp_cloud.connection_status()
    assert out["ready"] is False
    assert "review" in out["checks"][2]["detail"]


def test_status_flags_expired_token(monkeypatch):
    _status_settings(monkeypatch)
    expired = _Resp(401, {"error": {"code": 190, "message": "expired"}})
    monkeypatch.setattr(whatsapp_cloud.httpx, "get", _graph(expired, expired))
    out = whatsapp_cloud.connection_status()
    assert out["ready"] is False and "token" in out["checks"][1]["detail"]


def test_status_lists_missing_settings(monkeypatch):
    _configure(monkeypatch, token="")
    whatsapp_cloud.get_settings().whatsapp_business_account_id = ""
    monkeypatch.setattr(whatsapp_cloud.httpx, "get", lambda *a, **k: pytest.fail("must not call Meta"))
    out = whatsapp_cloud.connection_status()
    assert out["ready"] is False and "WHATSAPP_API_TOKEN" in out["checks"][0]["detail"]


def test_only_code_routes_exist():
    from core import config

    fields = config.Settings.model_fields
    assert not any(name.startswith("wa_checker") for name in fields)
    paths = {getattr(r, "path", "") for r in whatsapp_routes.router.routes}
    assert paths == {"/{shop_id}/whatsapp/send-code", "/{shop_id}/whatsapp/verify"}


# ---- changing the number resets what we knew about the old one ---------------------------------

class _Table:
    def __init__(self, log, data=None):
        self.log = log
        self.data = data if data is not None else [{"whatsapp_number": "+256700000001"}]

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
        return SimpleNamespace(data=self.data)


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
