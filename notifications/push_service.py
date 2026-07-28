"""Web Push (VAPID) delivery for browser notifications.

Uses the pywebpush library, which is pure-Python and works on Vercel's
serverless runtime. The VAPID keypair is configured via
`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` in the environment.

Keys may be stored as either:
  - URL-safe base64 (applicationServerKey / raw private) — preferred
  - PEM (`-----BEGIN … KEY-----`) — accepted and normalized at runtime

Browsers require the public key as an uncompressed P-256 point in
URL-safe base64. pywebpush's `Vapid.from_string` expects a raw 32-byte
private key (URL-safe base64) or DER — not PEM. We normalize both.

Public API:
    save_subscription(user_id, subscription, user_agent)
    delete_subscription_by_endpoint(endpoint)
    send_to_user(user_id, payload)     -> best-effort fan-out
    vapid_public_key()                  -> exposed to the browser via /public-key
    push_ready_status()                 -> why send may be skipped
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from db.supabase import get_supabase_admin
from core.config import get_settings

logger = logging.getLogger(__name__)


try:
    # pywebpush is optional at import time so the app still boots when the
    # VAPID keys are not set (dev / early deploys).
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover
    webpush = None  # type: ignore[assignment]
    WebPushException = Exception  # type: ignore[assignment]


class PushSubscriptionDict(dict):
    """Type stub for the subscription JSON sent by the browser."""


def _normalize_env_key(raw: str) -> str:
    """Strip quotes and turn escaped newlines into real ones (dotenv PEM)."""
    text = (raw or "").strip().strip('"').strip("'")
    if "\\n" in text and "BEGIN" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    return text.strip()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _application_server_key_from_pem(pem: str) -> str:
    """PEM SPKI public key → URL-safe base64 uncompressed EC point (65 bytes)."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_public_key(pem.encode("utf-8"), backend=default_backend())
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("VAPID public key must be an EC key")
    raw = key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url(raw)


def _raw_private_key_from_pem(pem: str) -> str:
    """PEM private key → URL-safe base64 of the 32-byte raw EC private value."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_private_key(
        pem.encode("utf-8"), password=None, backend=default_backend()
    )
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("VAPID private key must be an EC key")
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(raw)


def vapid_public_key() -> str:
    """Return the VAPID public key for `PushManager.subscribe`.

    Always URL-safe base64 (applicationServerKey). PEM env values are converted.
    """
    raw = _normalize_env_key(get_settings().vapid_public_key or "")
    if not raw:
        return ""
    if "BEGIN PUBLIC KEY" in raw or "BEGIN EC PUBLIC KEY" in raw:
        try:
            return _application_server_key_from_pem(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to convert VAPID_PUBLIC_KEY from PEM: %s", exc)
            return ""
    # Already applicationServerKey (typically ~87 chars, often starts with 'B')
    return raw.replace("\n", "").replace(" ", "")


def _vapid_private_key_for_webpush() -> str:
    """Private key string accepted by `pywebpush.webpush` / `Vapid.from_string`."""
    raw = _normalize_env_key(get_settings().vapid_private_key or "")
    if not raw:
        return ""
    if "BEGIN PRIVATE KEY" in raw or "BEGIN EC PRIVATE KEY" in raw:
        try:
            return _raw_private_key_from_pem(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to convert VAPID_PRIVATE_KEY from PEM: %s", exc)
            return ""
    return raw.replace("\n", "").replace(" ", "")


def push_ready_status() -> dict[str, Any]:
    """Diagnostics for /push/test — why delivery may return 0."""
    public = vapid_public_key()
    private = _vapid_private_key_for_webpush()
    claims = _vapid_claims()
    if webpush is None:
        return {"ready": False, "reason": "pywebpush_not_installed"}
    if not private:
        return {"ready": False, "reason": "VAPID_PRIVATE_KEY missing or invalid"}
    if not public:
        return {"ready": False, "reason": "VAPID_PUBLIC_KEY missing or invalid"}
    if not claims.get("sub"):
        return {"ready": False, "reason": "VAPID_CONTACT_EMAIL missing"}
    return {"ready": True, "reason": "ok"}


def _vapid_claims() -> dict[str, str]:
    """Standard VAPID claims. The `sub` MUST be a mailto: or https: URL."""
    contact = (get_settings().vapid_contact_email or "").strip()
    if not contact:
        return {}
    if not (contact.startswith("mailto:") or contact.startswith("https:")):
        contact = f"mailto:{contact}"
    return {"sub": contact}


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


def save_subscription(
    user_id: str,
    subscription: dict[str, Any],
    user_agent: str | None = None,
) -> dict[str, Any] | None:
    """Upsert a browser push subscription for a user.

    `subscription` is the object returned by `PushManager.subscribe()`, i.e.
        { "endpoint": "...", "keys": { "p256dh": "...", "auth": "..." } }
    Existing rows with the same endpoint are replaced (users often revoke and
    re-grant, which mints a new endpoint; this keeps the table tidy).
    """
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth_key = str(keys.get("auth") or "").strip()

    if not endpoint or not p256dh or not auth_key:
        return None

    admin = get_supabase_admin()
    payload = {
        "user_id": user_id,
        "endpoint": endpoint,
        "p256dh": p256dh,
        "auth": auth_key,
        "user_agent": (user_agent or "")[:512] or None,
    }
    try:
        # Try update-by-endpoint first (unique index) — this preserves the
        # original created_at when a user re-subscribes on the same device.
        existing = (
            admin.table("push_subscriptions")
            .select("id")
            .eq("endpoint", endpoint)
            .limit(1)
            .execute()
        )
        if existing.data:
            r = (
                admin.table("push_subscriptions")
                .update({
                    "user_id": user_id,
                    "p256dh": p256dh,
                    "auth": auth_key,
                    "user_agent": payload["user_agent"],
                })
                .eq("endpoint", endpoint)
                .execute()
            )
            return r.data[0] if r.data else None

        r = admin.table("push_subscriptions").insert(payload).execute()
        return r.data[0] if r.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_subscription failed for user %s: %s", user_id, exc)
        return None


def delete_subscription_by_endpoint(endpoint: str) -> None:
    """Best-effort removal, e.g. when the user unsubscribes or the push
    service reports the subscription is gone."""
    if not endpoint:
        return
    admin = get_supabase_admin()
    try:
        admin.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_subscription_by_endpoint failed: %s", exc)


def _list_user_subscriptions(user_id: str) -> list[dict[str, Any]]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("push_subscriptions")
            .select("id, endpoint, p256dh, auth")
            .eq("user_id", user_id)
            .execute()
        )
        return r.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("_list_user_subscriptions failed for %s: %s", user_id, exc)
        return []


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def send_to_user(user_id: str, payload: dict[str, Any]) -> int:
    """Deliver `payload` (JSON-serialisable) to every push subscription for
    the user. Returns the number of successful pushes.

    Dead subscriptions (410 Gone, 404 Not Found) are pruned as a side-effect.
    """
    if webpush is None:
        logger.warning("pywebpush not installed; skipping push for %s", user_id)
        return 0

    private_key = _vapid_private_key_for_webpush()
    if not private_key:
        logger.warning(
            "VAPID_PRIVATE_KEY missing/invalid; skipping push for %s", user_id
        )
        return 0

    claims = _vapid_claims()
    if not claims.get("sub"):
        logger.warning(
            "VAPID_CONTACT_EMAIL missing; skipping push for %s", user_id
        )
        return 0

    subs = _list_user_subscriptions(user_id)
    if not subs:
        return 0

    body = json.dumps(payload, separators=(",", ":"))

    successes = 0
    for sub in subs:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=body,
                vapid_private_key=private_key,
                vapid_claims=claims,
                ttl=60 * 60 * 24,  # 24h — push services will retry within this window
            )
            successes += 1
        except WebPushException as exc:  # type: ignore[misc]
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None) if resp is not None else None
            if status in (404, 410):
                delete_subscription_by_endpoint(sub["endpoint"])
                continue
            logger.warning(
                "webpush delivery failed (status=%s) for %s: %s",
                status,
                user_id,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("webpush unexpected error for %s: %s", user_id, exc)

    return successes
