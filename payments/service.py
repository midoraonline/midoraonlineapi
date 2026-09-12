"""Pesapal subscription + IPN handling.

Flow:
1. Merchant calls `/payments/subscribe` with a `plan_tier` → the amount and
   currency are looked up from `payments.plans`, a `subscriptions` row is
   persisted with `payment_status='PENDING'`, and (when Pesapal credentials
   are configured) a real `SubmitOrderRequest` is sent — with `account_number`
   (the shop id) and `subscription_details` so the customer can opt into
   Pesapal-native recurring billing — returning Pesapal's `redirect_url`.
   Free plans skip Pesapal entirely and activate immediately.
2. Pesapal redirects the shopper through checkout, then calls our IPN endpoint
   with `OrderTrackingId` + `OrderMerchantReference` (+ `OrderNotificationType`
   of `CALLBACKURL`/`IPNCHANGE` for the initial payment, or `RECURRING` for
   each subsequent auto-charge).
3. `process_webhook()` branches on that notification type. Initial payments
   are looked up by `merchant_reference`; recurring charges carry the shop id
   as `OrderMerchantReference` (our `account_number`) instead, so they're
   handled separately and renew whatever plan the shop last paid for. When
   Pesapal credentials are configured we verify status via Pesapal's API;
   otherwise we trust the payload (for dev/testing).
4. On a confirmed COMPLETED payment we flip `shops.is_active=true`, extend
   `subscription_end_date`, and promote the shop owner's `users.plan_tier` to
   the plan that was purchased. Failed/cancelled payments leave the shop and
   plan untouched.
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
from payments.plans import get_plan, is_valid_tier

logger = logging.getLogger(__name__)

COMPLETED_STATUSES = {"COMPLETED", "COMPLETE", "SUCCESS", "SUCCESSFUL", "1"}
FAILED_STATUSES = {"FAILED", "DECLINED", "CANCELLED", "CANCELED", "INVALID", "2", "3"}

_ipn_id_cache: dict[str, str] = {}


def _request_token(settings: Any) -> str | None:
    if not (settings.pesapal_consumer_key and settings.pesapal_consumer_secret):
        return None
    base = settings.pesapal_api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                f"{base}/api/Auth/RequestToken",
                json={
                    "consumer_key": settings.pesapal_consumer_key,
                    "consumer_secret": settings.pesapal_consumer_secret,
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            res.raise_for_status()
            return res.json().get("token")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pesapal RequestToken failed: %s", exc)
        return None


def _list_registered_ipns(settings: Any, token: str) -> list[dict]:
    base = settings.pesapal_api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.get(
                f"{base}/api/URLSetup/GetIpnList",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            )
            res.raise_for_status()
            data = res.json()
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("pesapal GetIpnList failed: %s", exc)
        return []


def _get_ipn_id(settings: Any, token: str) -> str | None:
    """Reuse an already-registered IPN id for our URL instead of re-registering
    on every process restart (the in-memory cache alone doesn't survive that)."""
    ipn_url = settings.pesapal_ipn_url
    if not ipn_url:
        return None
    if ipn_url in _ipn_id_cache:
        return _ipn_id_cache[ipn_url]

    for row in _list_registered_ipns(settings, token):
        if row.get("url") == ipn_url and row.get("ipn_id"):
            _ipn_id_cache[ipn_url] = row["ipn_id"]
            return row["ipn_id"]

    base = settings.pesapal_api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                f"{base}/api/URLSetup/RegisterIPN",
                json={"url": ipn_url, "ipn_notification_type": "POST"},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            res.raise_for_status()
            ipn_id = res.json().get("ipn_id")
            if ipn_id:
                _ipn_id_cache[ipn_url] = ipn_id
            return ipn_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("pesapal RegisterIPN failed: %s", exc)
        return None


def _frequency_for_days(days: int) -> str:
    if days <= 1:
        return "DAILY"
    if days <= 7:
        return "WEEKLY"
    if days <= 31:
        return "MONTHLY"
    return "YEARLY"


def _format_ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def _submit_order(
    settings: Any,
    token: str,
    ipn_id: str,
    merchant_reference: str,
    amount: float,
    currency: str,
    plan_name: str,
    billing_address: dict,
    account_number: str | None = None,
    subscription_details: dict | None = None,
) -> dict | None:
    base = settings.pesapal_api_base_url.rstrip("/")
    callback_url = f"{settings.frontend_public_url.rstrip('/')}/merchant/billing" if settings.frontend_public_url else ""
    body: dict[str, Any] = {
        "id": merchant_reference,
        "currency": currency,
        "amount": amount,
        "description": f"Midora {plan_name} plan subscription"[:100],
        "callback_url": callback_url,
        "notification_id": ipn_id,
        "billing_address": billing_address,
    }
    if account_number:
        body["account_number"] = account_number
    if subscription_details:
        body["subscription_details"] = subscription_details
    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.post(
                f"{base}/api/Transactions/SubmitOrderRequest",
                json=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            res.raise_for_status()
            return res.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pesapal SubmitOrderRequest failed: %s", exc)
        return None


def _billing_address_for_owner(admin: Any, shop_id: str) -> dict:
    address = {"email_address": "", "phone_number": "", "country_code": "UG", "first_name": "", "last_name": ""}
    shop_r = admin.table("shops").select("owner_id,name").eq("id", shop_id).limit(1).execute()
    if not shop_r.data:
        return address
    owner_id = shop_r.data[0].get("owner_id")
    address["last_name"] = shop_r.data[0].get("name") or ""
    if not owner_id:
        return address
    user_r = admin.table("users").select("email,full_name,phone_number").eq("id", owner_id).limit(1).execute()
    if user_r.data:
        user = user_r.data[0]
        address["email_address"] = user.get("email") or ""
        address["phone_number"] = user.get("phone_number") or ""
        address["first_name"] = user.get("full_name") or ""
    return address


def create_subscription_intent(shop_id: str, plan_tier: str) -> dict:
    """Create a subscription record and, for paid plans, submit a real Pesapal order."""
    if not is_valid_tier(plan_tier):
        raise ValueError(f"Unknown plan_tier: {plan_tier}")
    plan = get_plan(plan_tier)
    amount = float(plan["price_ugx"])
    currency = plan["currency"]
    settings = get_settings()
    merchant_reference = f"sub-{shop_id}-{uuid.uuid4().hex[:8]}"
    admin = get_supabase_admin()

    if amount <= 0:
        admin.table("subscriptions").insert(
            {
                "shop_id": shop_id,
                "merchant_reference": merchant_reference,
                "amount": amount,
                "currency": currency,
                "payment_status": "COMPLETED",
                "plan_tier": plan_tier,
            }
        ).execute()
        _apply_plan_to_owner(admin, shop_id, plan_tier)
        return {"redirect_url": None, "merchant_reference": merchant_reference, "plan_tier": plan_tier}

    admin.table("subscriptions").insert(
        {
            "shop_id": shop_id,
            "merchant_reference": merchant_reference,
            "amount": amount,
            "currency": currency,
            "payment_status": "PENDING",
            "plan_tier": plan_tier,
        }
    ).execute()

    token = _request_token(settings)
    order = None
    if token:
        ipn_id = _get_ipn_id(settings, token)
        if ipn_id:
            billing_address = _billing_address_for_owner(admin, shop_id)
            now = datetime.now(timezone.utc)
            billing_days = int(plan.get("billing_period_days") or 30)
            subscription_details = {
                "start_date": _format_ddmmyyyy(now),
                "end_date": _format_ddmmyyyy(now + timedelta(days=365 * 3)),
                "frequency": _frequency_for_days(billing_days),
            }
            order = _submit_order(
                settings,
                token,
                ipn_id,
                merchant_reference,
                amount,
                currency,
                plan["name"],
                billing_address,
                account_number=shop_id,
                subscription_details=subscription_details,
            )

    if order and order.get("redirect_url"):
        admin.table("subscriptions").update(
            {"pesapal_order_tracking_id": order.get("order_tracking_id")}
        ).eq("merchant_reference", merchant_reference).execute()
        return {
            "redirect_url": order["redirect_url"],
            "merchant_reference": merchant_reference,
            "plan_tier": plan_tier,
        }

    base = settings.pesapal_api_base_url.rstrip("/")
    return {
        "redirect_url": f"{base}/pay?ref={merchant_reference}",
        "merchant_reference": merchant_reference,
        "plan_tier": plan_tier,
    }


def _apply_plan_to_owner(admin: Any, shop_id: str, plan_tier: str) -> None:
    plan = get_plan(plan_tier)
    shop_r = admin.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
    if not shop_r.data:
        return
    owner_id = shop_r.data[0].get("owner_id")
    if not owner_id:
        return
    settings = get_settings()
    end_at = datetime.now(timezone.utc) + timedelta(
        days=plan.get("billing_period_days") or max(1, settings.subscription_duration_days)
    )
    admin.table("users").update(
        {"plan_tier": plan_tier, "plan_expires_at": end_at.isoformat()}
    ).eq("id", owner_id).execute()


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


def _extract_notification_type(payload: dict) -> str | None:
    raw = payload.get("OrderNotificationType") or payload.get("orderNotificationType")
    return str(raw).upper().strip() if raw else None


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


def _process_recurring_charge(admin: Any, payload: dict, tracking_id: str | None) -> bool:
    """Handle a Pesapal `OrderNotificationType=RECURRING` IPN.

    Recurring charges reuse the `account_number` we set to the shop id in
    `SubmitOrderRequest` as `OrderMerchantReference` instead of our original
    one-off `merchant_reference`, so they're looked up by shop id and renew
    whatever plan that shop last paid for.
    """
    shop_id = payload.get("OrderMerchantReference") or payload.get("orderMerchantReference")
    if not shop_id or not tracking_id:
        logger.warning("pesapal recurring webhook missing shop_id/tracking_id: %s", payload)
        return False

    status = _fetch_live_status(tracking_id) or ""
    if status not in COMPLETED_STATUSES:
        logger.info("pesapal recurring charge %s not completed (status=%s)", tracking_id, status)
        return True

    last = (
        admin.table("subscriptions")
        .select("plan_tier, amount, currency")
        .eq("shop_id", shop_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not last.data:
        logger.warning("pesapal recurring charge for unknown shop %s", shop_id)
        return False
    prior = last.data[0]
    plan_tier = prior.get("plan_tier") or "basic"

    try:
        admin.table("subscriptions").insert(
            {
                "shop_id": shop_id,
                "merchant_reference": f"rec-{tracking_id}",
                "amount": prior.get("amount") or 0,
                "currency": prior.get("currency") or "UGX",
                "payment_status": "COMPLETED",
                "plan_tier": plan_tier,
                "pesapal_order_tracking_id": tracking_id,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.info("pesapal recurring charge %s already recorded: %s", tracking_id, exc)
        return True

    settings = get_settings()
    end_at = datetime.now(timezone.utc) + timedelta(days=max(1, settings.subscription_duration_days))
    admin.table("shops").update(
        {"is_active": True, "subscription_end_date": end_at.isoformat()}
    ).eq("id", shop_id).execute()
    _apply_plan_to_owner(admin, shop_id, plan_tier)
    logger.info("renewed shop %s plan %s via pesapal recurring charge %s", shop_id, plan_tier, tracking_id)
    return True


def process_webhook(payload: dict) -> bool:
    """Verify and apply a Pesapal IPN payload. Returns True when processed."""
    if not payload:
        return False

    admin = get_supabase_admin()
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

    if _extract_notification_type(payload) == "RECURRING":
        handled = _process_recurring_charge(admin, payload, tracking_id)
        _mark_log_processed(admin, log_id)
        return handled

    reference = _extract_reference(payload)
    if not reference:
        logger.warning("pesapal webhook missing reference: %s", payload)
        return False

    subs = (
        admin.table("subscriptions")
        .select("id, shop_id, payment_status, plan_tier")
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
        _apply_plan_to_owner(admin, shop_id, sub.get("plan_tier") or "basic")
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
