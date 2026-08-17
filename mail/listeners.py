"""Merchant confirmation + status change mail listeners."""
from __future__ import annotations

import logging

from common.events.payloads import (
    ProductPostedEvent,
    ProductStatusChangedEvent,
    ShopVerificationChangedEvent,
)
from core.config import get_settings
from core.db import get_supabase_admin
from mail.queue import enqueue_mail, filter_recipients, get_admin_emails
from mail.templates import (
    build_new_product_admin_notification,
    build_product_submitted_confirmation,
    render_listing_decision,
    render_shop_verification_decision,
)

logger = logging.getLogger(__name__)


async def on_product_created(event: ProductPostedEvent) -> None:
    admin = get_supabase_admin()
    merchant_email: str | None = None

    if event.seller_id:
        try:
            user_r = (
                admin.table("users")
                .select("email")
                .eq("id", event.seller_id)
                .limit(1)
                .execute()
            )
            if user_r.data and user_r.data[0].get("email"):
                merchant_email = user_r.data[0]["email"]
        except Exception as exc:
            logger.warning("lookup merchant email failed for user %s: %s", event.seller_id, exc)

    if merchant_email:
        subject, body_html = build_product_submitted_confirmation(product_title=event.title)
        try:
            await enqueue_mail(to=merchant_email, subject=subject, body_html=body_html)
        except Exception as exc:
            logger.warning("enqueue merchant confirmation failed: %s", exc)

    recipients = filter_recipients(get_admin_emails(), merchant_email)
    if not recipients:
        return

    shop_name = "Unknown"
    if event.shop_id:
        try:
            shop_row = (
                admin.table("shops")
                .select("name")
                .eq("id", event.shop_id)
                .limit(1)
                .execute()
            )
            if shop_row.data:
                shop_name = shop_row.data[0].get("name", "Unknown")
        except Exception as exc:
            logger.warning("lookup shop name failed for %s: %s", event.shop_id, exc)

    settings = get_settings()
    admin_url = f"{settings.frontend_public_url}/admin/listings"
    subject, body_html = build_new_product_admin_notification(
        shop_name=shop_name,
        product_title=event.title,
        price_ugx=event.price_ugx,
        category=event.category,
        admin_listings_url=admin_url,
    )
    for recipient in recipients:
        try:
            await enqueue_mail(to=recipient, subject=subject, body_html=body_html)
        except Exception as exc:
            logger.warning("enqueue admin notification failed for %s: %s", recipient, exc)


async def on_product_status_changed(event: ProductStatusChangedEvent) -> None:
    """Send listing approval/rejection decision email to merchant on status change."""
    admin = get_supabase_admin()
    merchant_email: str | None = None

    if event.seller_id:
        try:
            user_r = admin.table("users").select("email").eq("id", event.seller_id).limit(1).execute()
            if user_r.data and user_r.data[0].get("email"):
                merchant_email = user_r.data[0]["email"]
        except Exception as exc:
            logger.warning("on_product_status_changed: email lookup failed for seller %s: %s", event.seller_id, exc)

    if not merchant_email and event.shop_id:
        try:
            shop_r = admin.table("shops").select("owner_id").eq("id", event.shop_id).limit(1).execute()
            if shop_r.data and shop_r.data[0].get("owner_id"):
                owner_id = shop_r.data[0]["owner_id"]
                user_r = admin.table("users").select("email").eq("id", owner_id).limit(1).execute()
                if user_r.data and user_r.data[0].get("email"):
                    merchant_email = user_r.data[0]["email"]
        except Exception as exc:
            logger.warning("on_product_status_changed: email lookup failed for shop %s: %s", event.shop_id, exc)

    if not merchant_email:
        return

    status_map = {
        "active": "approved",
        "rejected": "rejected",
        "pending_review": "needs_review",
    }
    decision = status_map.get(event.new_status)
    if not decision:
        return  # skip internal status changes like hidden/draft

    settings = get_settings()
    listings_url = f"{settings.frontend_public_url.rstrip('/')}/merchant" if getattr(settings, "frontend_public_url", "") else None
    subject, html = render_listing_decision(
        product_title=event.title or "Your listing",
        decision=decision,
        reason=event.reason,
        listings_url=listings_url,
    )
    try:
        await enqueue_mail(to=merchant_email, subject=subject, body_html=html)
    except Exception as exc:
        logger.warning("on_product_status_changed: enqueue_mail failed for %s: %s", merchant_email, exc)


async def on_shop_verification_changed(event: ShopVerificationChangedEvent) -> None:
    """Send verification outcome email to merchant when verification status updates."""
    admin = get_supabase_admin()
    merchant_email: str | None = None
    shop_name = "Your Shop"

    if event.shop_id:
        try:
            shop_r = admin.table("shops").select("owner_id, shop_email, name").eq("id", event.shop_id).limit(1).execute()
            if shop_r.data:
                shop_row = shop_r.data[0]
                shop_name = shop_row.get("name") or shop_name
                merchant_email = shop_row.get("shop_email")
                if not merchant_email and shop_row.get("owner_id"):
                    user_r = admin.table("users").select("email").eq("id", shop_row["owner_id"]).limit(1).execute()
                    if user_r.data and user_r.data[0].get("email"):
                        merchant_email = user_r.data[0]["email"]
        except Exception as exc:
            logger.warning("on_shop_verification_changed: shop lookup failed for %s: %s", event.shop_id, exc)

    if not merchant_email and event.owner_id:
        try:
            user_r = admin.table("users").select("email").eq("id", event.owner_id).limit(1).execute()
            if user_r.data and user_r.data[0].get("email"):
                merchant_email = user_r.data[0]["email"]
        except Exception as exc:
            logger.warning("on_shop_verification_changed: email lookup failed for owner %s: %s", event.owner_id, exc)

    if not merchant_email:
        return

    decision = "verified" if event.new_status == "verified" else "rejected"
    subject, html = render_shop_verification_decision(
        shop_name=shop_name,
        decision=decision,
        notes=event.reason,
    )
    try:
        await enqueue_mail(to=merchant_email, subject=subject, body_html=html)
    except Exception as exc:
        logger.warning("on_shop_verification_changed: enqueue_mail failed for %s: %s", merchant_email, exc)
