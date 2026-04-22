"""Pesapal subscription + IPN handling.

Flow:
1. Merchant calls `/payments/subscribe` → we persist a `subscriptions` row with
   `payment_status='PENDING'` and return a redirect URL to Pesapal.
2. Pesapal redirects the shopper through checkout, then calls our IPN endpoint
   with `OrderTrackingId` + `OrderMerchantReference` (+ optional status).
3. `process_webhook()` looks up the subscription by merchant_reference. When
   Pesapal credentials are configured we verify status via Pesapal's API;
   otherwise we trust the payload (for dev/testing).
4. On a confirmed COMPLETED payment we flip `shops.is_active=true` and extend
   `subscription_end_date` by `SUBSCRIPTION_DURATION_DAYS`. Failed/cancelled
   payments leave the shop untouched.
5. Every IPN call is logged to `pesapal_webhook_logs` for audit + idempotency.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from core.config import get_settings
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

COMPLETED_STATUSES = {"COMPLETED", "COMPLETE", "SUCCESS", "SUCCESSFUL", "1"}
FAILED_STATUSES = {"FAILED", "DECLINED", "CANCELLED", "CANCELED", "INVALID", "2", "3"}


def create_subscription_intent(
    shop_id: str, amount: float = 5000.0, currency: str = "UGX"
) -> dict:
    """Create subscription record (PENDING) and return redirect URL for Pesapal.

    Note: the redirect URL is a placeholder — the real Pesapal order submission
    happens client-side after the checkout URL is generated. This can be
    extended once we wire up `SubmitOrderRequest`.
    """
    settings = get_settings()
    merchant_reference = f"sub-{shop_id}-{uuid.uuid4().hex[:8]}"
    admin = get_supabase_admin()
    admin.table("subscriptions").insert(
        {
            "shop_id": shop_id,
            "merchant_reference": merchant_reference,
            "amount": amount,
            "currency": currency,
            "payment_status": "PENDING",
        }
    ).execute()
    base = settings.pesapal_api_base_url.rstrip("/")
    return {
        "redirect_url": f"{base}/pay?ref={merchant_reference}",
        "merchant_reference": merchant_reference,
    }


def list_subscriptions_for_user(client: Any) -> list:
    """List subscriptions (RLS: merchant sees own shop's). When client has user JWT, RLS filters."""
    r = (
        client.table("subscriptions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return r.data or []


# ---------------------------------------------------------------------------
# IPN handling
# ---------------------------------------------------------------------------


def _extract_reference(payload: dict) -> str | None:
    return (
        payload.get("OrderMerchantReference")
        or payload.get("MerchantReference")
        or payload.get("merchant_reference")
        or payload.get("OrderTrackingId")
    )


def _extract_tracking_id(payload: dict) -> str | None:
    return payload.get("OrderTrackingId") or payload.get("order_tracking_id")


def _extract_status(payload: dict) -> str | None:
    raw = (
        payload.get("payment_status_description")
        or payload.get("status")
        or payload.get("PaymentStatus")
        or payload.get("payment_status")
        or payload.get("status_code")
    )
    if raw is None:
        return None
    return str(raw).upper().strip()


def _fetch_live_status(tracking_id: str) -> str | None:
    """Query Pesapal GetTransactionStatus for the live payment status.

    Returns the uppercased status string, or `None` if credentials are absent
    or the request fails (in which case we fall back to the IPN payload).
    """
    settings = get_settings()
    if not (settings.pesapal_consumer_key and settings.pesapal_consumer_secret):
        return None
    base = settings.pesapal_api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            token_res = client.post(
                f"{base}/api/Auth/RequestToken",
                json={
                    "consumer_key": settings.pesapal_consumer_key,
                    "consumer_secret": settings.pesapal_consumer_secret,
                },
                headers={"Accept": "application/json"},
            )
            token_res.raise_for_status()
            token = token_res.json().get("token")
            if not token:
                return None
            status_res = client.get(
                f"{base}/api/Transactions/GetTransactionStatus",
                params={"orderTrackingId": tracking_id},
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            status_res.raise_for_status()
            data = status_res.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pesapal status lookup failed for %s: %s", tracking_id, exc)
        return None

    return (
        str(
            data.get("payment_status_description")
            or data.get("status")
            or data.get("status_code")
            or ""
        )
        .upper()
        .strip()
        or None
    )


def process_webhook(payload: dict) -> bool:
    """Verify and apply a Pesapal IPN payload. Returns True when processed."""
    if not payload:
        return False

    admin = get_supabase_admin()
    reference = _extract_reference(payload)
    tracking_id = _extract_tracking_id(payload)

    log_id: str | None = None
    try:
        log_r = (
            admin.table("pesapal_webhook_logs")
            .insert({"payload": payload, "processed": False})
            .execute()
        )
        if log_r.data:
            log_id = log_r.data[0].get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to log pesapal webhook: %s", exc)

    if not reference:
        logger.warning("pesapal webhook missing reference: %s", payload)
        return False

    subs = (
        admin.table("subscriptions")
        .select("id, shop_id, payment_status")
        .eq("merchant_reference", reference)
        .limit(1)
        .execute()
    )
    if not subs.data:
        logger.warning("pesapal webhook: no subscription for ref %s", reference)
        _mark_log_processed(admin, log_id)
        return False

    sub = subs.data[0]
    sub_id = sub["id"]
    shop_id = sub["shop_id"]
    current_status = (sub.get("payment_status") or "").upper()

    if current_status == "COMPLETED":
        _mark_log_processed(admin, log_id)
        return True

    live_status = _fetch_live_status(tracking_id) if tracking_id else None
    derived_status = live_status or _extract_status(payload) or ""

    if derived_status in COMPLETED_STATUSES:
        admin.table("subscriptions").update(
            {
                "payment_status": "COMPLETED",
                "pesapal_order_tracking_id": tracking_id,
            }
        ).eq("id", sub_id).execute()

        settings = get_settings()
        end_at = datetime.now(timezone.utc) + timedelta(
            days=max(1, settings.subscription_duration_days)
        )
        admin.table("shops").update(
            {"is_active": True, "subscription_end_date": end_at.isoformat()}
        ).eq("id", shop_id).execute()
        logger.info(
            "activated shop %s via pesapal ref %s until %s",
            shop_id,
            reference,
            end_at.isoformat(),
        )
    elif derived_status in FAILED_STATUSES:
        admin.table("subscriptions").update(
            {
                "payment_status": "FAILED",
                "pesapal_order_tracking_id": tracking_id,
            }
        ).eq("id", sub_id).execute()
        logger.info(
            "pesapal ref %s marked failed (status=%s)", reference, derived_status
        )
    else:
        logger.info(
            "pesapal ref %s pending (status=%s)", reference, derived_status or "?"
        )

    _mark_log_processed(admin, log_id)
    return True


def _mark_log_processed(admin: Any, log_id: str | None) -> None:
    if not log_id:
        return
    try:
        admin.table("pesapal_webhook_logs").update({"processed": True}).eq(
            "id", log_id
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not flag pesapal log %s processed: %s", log_id, exc)
