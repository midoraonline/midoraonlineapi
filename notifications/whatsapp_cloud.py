"""WhatsApp Business Cloud API (Meta): sends the shop-number OTP as an authentication template.

A 200 only means Meta accepted the message. A number that is not on WhatsApp is reported later
(webhook error 131026), so ownership is proven only when the owner types the code back.
"""

from __future__ import annotations

import logging

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    s = get_settings()
    return bool(s.whatsapp_api_token.strip() and s.whatsapp_phone_number_id.strip() and s.whatsapp_message_template.strip())


def otp_payload(e164: str, code: str, template: str, language: str) -> dict:
    # Authentication templates need the code twice: in the body and in the copy-code button.
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": e164.lstrip("+"),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": code}]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]},
            ],
        },
    }


def send_otp(e164: str, code: str) -> str | None:
    """Send the code. Returns Meta's message id (wamid…) or None when the send was not accepted."""
    if not is_configured():
        logger.warning("WhatsApp Cloud API is not configured")
        return None
    s = get_settings()
    url = f"{s.whatsapp_api_url.rstrip('/')}/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"
    try:
        response = httpx.post(
            url,
            json=otp_payload(e164, code, s.whatsapp_message_template, s.whatsapp_template_language),
            headers={"Authorization": f"Bearer {s.whatsapp_api_token}"},
            timeout=s.whatsapp_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp Cloud API unreachable: %s", exc)
        return None
    if response.status_code != 200:
        logger.warning("WhatsApp Cloud API rejected OTP: %s %s", response.status_code, response.text[:300])
        return None
    messages = (response.json() or {}).get("messages") or []
    message_id = messages[0].get("id") if messages else None
    if not message_id:
        logger.warning("WhatsApp Cloud API returned no message id: %s", response.text[:300])
    return message_id


def _graph_get(path: str, params: dict) -> tuple[dict | None, str | None]:
    """(body, error) for a read-only Graph API call."""
    s = get_settings()
    try:
        response = httpx.get(
            f"{s.whatsapp_api_url.rstrip('/')}/{s.whatsapp_api_version}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {s.whatsapp_api_token}"},
            timeout=s.whatsapp_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        return None, f"Meta could not be reached: {exc.__class__.__name__}"
    body = response.json() if response.content else {}
    if response.status_code != 200:
        err = (body or {}).get("error") or {}
        if err.get("code") == 190:
            return None, "The access token is invalid or expired. Create a permanent system-user token."
        return None, err.get("message") or f"Meta answered {response.status_code}"
    return body, None


def _check(key: str, label: str, ok: bool, detail: str) -> dict:
    return {"key": key, "label": label, "ok": ok, "detail": detail}


def connection_status() -> dict:
    """Read-only health of the Cloud API setup: config, token + phone number, OTP template."""
    s = get_settings()
    missing = [
        name
        for name, value in (
            ("WHATSAPP_API_TOKEN", s.whatsapp_api_token),
            ("WHATSAPP_PHONE_NUMBER_ID", s.whatsapp_phone_number_id),
            ("WHATSAPP_MESSAGE_TEMPLATE", s.whatsapp_message_template),
            ("WHATSAPP_MESSAGING_ACCOUNT_ID", s.whatsapp_business_account_id),
        )
        if not value.strip()
    ]
    checks = [
        _check("config", "Settings", not missing, f"Missing: {', '.join(missing)}" if missing else "All settings present."),
    ]
    phone: dict | None = None
    template: dict | None = None
    if not missing:
        phone, err = _graph_get(
            s.whatsapp_phone_number_id,
            {"fields": "display_phone_number,verified_name,quality_rating,name_status,code_verification_status,messaging_limit_tier"},
        )
        if err:
            checks.append(_check("phone", "Token and phone number", False, err))
        else:
            name = phone.get("verified_name") or "Unnamed"
            checks.append(_check("phone", "Token and phone number", True, f"{phone.get('display_phone_number')} ({name})"))

        body, err = _graph_get(
            f"{s.whatsapp_business_account_id}/message_templates",
            {"name": s.whatsapp_message_template, "fields": "name,status,category,language,rejected_reason"},
        )
        if err:
            checks.append(_check("template", "Code template", False, err))
        else:
            rows = [r for r in (body.get("data") or []) if r.get("name") == s.whatsapp_message_template]
            template = rows[0] if rows else None
            if template is None:
                detail, ok = f"No template named '{s.whatsapp_message_template}' on this account.", False
            else:
                status = str(template.get("status") or "UNKNOWN").upper()
                ok = status == "APPROVED"
                reasons = {
                    "PENDING": "Waiting for Meta to review it. Codes can't be sent until it is approved.",
                    "REJECTED": f"Meta rejected it ({template.get('rejected_reason') or 'no reason given'}). Edit and resubmit.",
                    "PAUSED": "Paused by Meta after low quality feedback.",
                    "DISABLED": "Disabled by Meta.",
                }
                detail = "Approved. Codes can be sent." if ok else reasons.get(status, f"Status: {status}.")
                if ok and str(template.get("category") or "").upper() != "AUTHENTICATION":
                    ok, detail = False, "Approved, but it must be an AUTHENTICATION template with a copy-code button."
            checks.append(_check("template", "Code template", ok, detail))

    return {
        "ready": all(c["ok"] for c in checks) and len(checks) == 3,
        "checks": checks,
        "phone": phone,
        "template": template,
    }
