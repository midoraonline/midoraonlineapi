"""Africa's Talking SMS + WhatsApp senders (used for OTP verification)."""

from __future__ import annotations

import logging

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

# Sandbox apps can't send real SMS/WhatsApp (AT's sandbox send endpoints 404) — always use the live host.
_SMS_URL = "https://api.africastalking.com/version1/messaging/bulk"
_WHATSAPP_URL = "https://chat.africastalking.com/whatsapp/message/send"
_TIMEOUT_SECONDS = 10.0


def _warn_if_sandbox(username: str, channel: str) -> None:
    if username.strip().lower() == "sandbox":
        logger.warning("AFRICASTALKING_USERNAME is 'sandbox' — %s won't actually deliver; use a live app.", channel)


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

    _warn_if_sandbox(settings.africastalking_username, "SMS")
    try:
        r = httpx.post(
            _SMS_URL,
            json=payload,
            headers={
                "apiKey": settings.africastalking_api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        return True
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
    _warn_if_sandbox(settings.africastalking_username, "WhatsApp")
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
