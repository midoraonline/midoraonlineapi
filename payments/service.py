import uuid
from typing import Any

from core.config import get_settings
from db.supabase import get_supabase_admin


def create_subscription_intent(shop_id: str, amount: float = 5000.0, currency: str = "UGX") -> dict:
    """Create subscription record (PENDING) and return redirect URL for Pesapal. Stub: real impl calls Pesapal API."""
    settings = get_settings()
    merchant_reference = f"sub-{shop_id}-{uuid.uuid4().hex[:8]}"
    admin = get_supabase_admin()
    admin.table("subscriptions").insert({
        "shop_id": shop_id,
        "merchant_reference": merchant_reference,
        "amount": amount,
        "currency": currency,
        "payment_status": "PENDING",
    }).execute()
    return {"redirect_url": f"https://pay.pesapal.com/...?ref={merchant_reference}", "merchant_reference": merchant_reference}


def list_subscriptions_for_user(client: Any) -> list:
    """List subscriptions (RLS: merchant sees own shop's). When client has user JWT, RLS filters."""
    r = client.table("subscriptions").select("*").order("created_at", desc=True).execute()
    return r.data or []


def process_webhook(payload: dict) -> bool:
    """Verify signature, log, idempotency check, update subscriptions and shops. Returns True if processed."""
    admin = get_supabase_admin()
    ref = payload.get("OrderTrackingId") or payload.get("MerchantReference") or payload.get("merchant_reference")
    if not ref:
        return False
    # Idempotency: already processed this ref?
    subs = admin.table("subscriptions").select("id, shop_id, payment_status").eq("merchant_reference", ref).execute()
    if subs.data and len(subs.data) > 0 and subs.data[0].get("payment_status") == "COMPLETED":
        return True
    # Log
    log_r = admin.table("pesapal_webhook_logs").insert({"payload": payload, "processed": False}).execute()
    log_id = log_r.data[0]["id"] if log_r.data else None
    # TODO: verify Pesapal signature
    if subs.data and len(subs.data) > 0:
        sub_id = subs.data[0]["id"]
        shop_id = subs.data[0]["shop_id"]
        admin.table("subscriptions").update({"payment_status": "COMPLETED"}).eq("id", sub_id).execute()
        from datetime import datetime, timezone, timedelta
        admin.table("shops").update({
            "is_active": True,
            "subscription_end_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }).eq("id", shop_id).execute()
    if log_id:
        admin.table("pesapal_webhook_logs").update({"processed": True}).eq("id", log_id).execute()
    return True
