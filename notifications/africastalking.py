"""Africa's Talking SMS + WhatsApp senders (used for OTP verification)."""

from __future__ import annotations

import logging

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

# Sandbox apps must use the sandbox host. Live apps stay on the production host.
def _sms_url(username: str) -> str:
    host = (
        "https://api.sandbox.africastalking.com"
        if username.strip().lower() == "sandbox"
        else "https://api.africastalking.com"
    )
    return f"{host}/version1/messaging/bulk"


_WHATSAPP_URL = "https://chat.africastalking.com/whatsapp/message/send"
_TIMEOUT_SECONDS = 10.0


# Recipient statusCode 100 Processed, 101 Sent, 102 Queued. Anything else
# (401 RiskHold, 402 InvalidSenderId, 403 InvalidPhoneNumber,
# 405 InsufficientBalance, 406 UserInBlacklist, ...) means no SMS goes out,
# even though the HTTP status is 201.
_SMS_OK = {100, 101, 102}


def sms_accepted(response: httpx.Response) -> bool:
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - non-JSON success body; trust HTTP status
        return True
    recipients = ((data or {}).get("SMSMessageData") or {}).get("Recipients")
    if not isinstance(recipients, list):
        return True
    if not recipients:
        logger.warning(
            "Africa's Talking SMS rejected: %s",
            ((data or {}).get("SMSMessageData") or {}).get("Message"),
        )
        return False
    ok = True
    for rec in recipients:
        code = rec.get("statusCode")
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = None
        if code not in _SMS_OK:
            ok = False
            logger.warning(
                "Africa's Talking SMS not delivered: status=%s code=%s",
                rec.get("status"),
                rec.get("statusCode"),
            )
    return ok


def send_sms(phone_number: str, message: str) -> bool:
    """Send a plain SMS via Africa's Talking Bulk SMS. Returns True on success."""
    settings = get_settings()
    if not settings.africastalking_api_key or not settings.africastalking_username:
        logger.warning("Africa's Talking SMS not configured; skipping send")
        return False

    payload: dict[str, object] = {
        "username": settings.africastalking_username,
        "message": message,
        "phoneNumbers": [phone_number],
    }
    if settings.africastalking_sender_id:
        payload["senderId"] = settings.africastalking_sender_id

    try:
        r = httpx.post(
            _sms_url(settings.africastalking_username),
            json=payload,
            headers={
                "apiKey": settings.africastalking_api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        return sms_accepted(r)
    except httpx.HTTPStatusError as exc:
        logger.warning("Africa's Talking SMS send failed: %s — %s", exc, exc.response.text)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Africa's Talking SMS send failed: %s", exc)
        return False


def send_whatsapp_text(phone_number: str, message: str) -> bool:
    """Send a plain WhatsApp text message via Africa's Talking. Returns True on success."""
    settings = get_settings()
    if not settings.africastalking_api_key or not settings.africastalking_username:
        logger.warning("Africa's Talking WhatsApp not configured; skipping send")
        return False
    if not settings.africastalking_whatsapp_number:
        logger.warning("AFRICASTALKING_WHATSAPP_NUMBER not set; cannot send WhatsApp message")
        return False

    payload = {
        "username": settings.africastalking_username,
        "waNumber": settings.africastalking_whatsapp_number,
        "phoneNumber": phone_number,
        "body": {"message": message},
    }
    try:
        r = httpx.post(
            _WHATSAPP_URL,
            json=payload,
            headers={
                "apikey": settings.africastalking_api_key,
                "content-type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.warning("Africa's Talking WhatsApp send failed: %s — %s", exc, exc.response.text)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Africa's Talking WhatsApp send failed: %s", exc)
        return False
