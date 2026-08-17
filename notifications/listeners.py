"""In-app and push notification listeners for product status and shop verification changes."""
from __future__ import annotations

import logging

from common.events.payloads import (
    ProductStatusChangedEvent,
    ShopVerificationChangedEvent,
)
from core.db import get_supabase_admin
from notifications.push_service import send_to_user
from notifications.service import create_notification

logger = logging.getLogger(__name__)


def _resolve_seller_id(seller_id: str | None, shop_id: str | None) -> str | None:
    if seller_id:
        return seller_id
    if not shop_id:
        return None
    try:
        admin = get_supabase_admin()
        sr = admin.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
        if sr.data and sr.data[0].get("owner_id"):
            return str(sr.data[0]["owner_id"])
    except Exception as exc:
        logger.warning("failed to resolve owner_id for shop %s: %s", shop_id, exc)
    return None


async def on_product_status_changed(event: ProductStatusChangedEvent) -> None:
    """Send in-app notification and web push when product status updates."""
    user_id = _resolve_seller_id(event.seller_id, event.shop_id)
    if not user_id:
        return

    title_str = event.title or "Your listing"
    if event.new_status == "active":
        notif_title = "Listing Approved"
        notif_body = f"Good news! '{title_str}' is now live on Midora."
    elif event.new_status == "rejected":
        notif_title = "Listing Not Approved"
        reason_text = f" Reason: {event.reason}" if event.reason else ""
        notif_body = f"Your listing '{title_str}' was not approved.{reason_text}"
    elif event.new_status == "pending_review":
        notif_title = "Listing Under Review"
        notif_body = f"'{title_str}' is currently under review by our team."
    else:
        return

    try:
        create_notification(
            user_id=user_id,
            title=notif_title,
            body=notif_body,
            channel="in-app",
            metadata={
                "type": "product_status_change",
                "product_id": event.product_id,
                "status": event.new_status,
            },
        )
    except Exception as exc:
        logger.warning("create_notification failed for user %s: %s", user_id, exc)

    try:
        send_to_user(
            user_id=user_id,
            payload={
                "title": notif_title,
                "body": notif_body,
                "data": {"product_id": event.product_id, "status": event.new_status},
            },
        )
    except Exception as exc:
        logger.warning("send_to_user push failed for user %s: %s", user_id, exc)


async def on_shop_verification_changed(event: ShopVerificationChangedEvent) -> None:
    """Send in-app notification and web push when shop verification status updates."""
    owner_id = event.owner_id
    if not owner_id and event.shop_id:
        owner_id = _resolve_seller_id(None, event.shop_id)

    if not owner_id:
        return

    shop_name = event.shop.get("name") if isinstance(event.shop, dict) else "Your shop"
    if event.new_status == "verified":
        notif_title = "Verification Approved"
        notif_body = f"Stage {event.stage} verification for '{shop_name}' has been approved!"
    elif event.new_status == "rejected":
        notif_title = "Verification Update"
        reason_text = f" Reason: {event.reason}" if event.reason else ""
        notif_body = f"Stage {event.stage} verification for '{shop_name}' was not approved.{reason_text}"
    else:
        return

    try:
        create_notification(
            user_id=owner_id,
            title=notif_title,
            body=notif_body,
            channel="in-app",
            metadata={
                "type": "shop_verification_change",
                "shop_id": event.shop_id,
                "stage": event.stage,
                "status": event.new_status,
            },
        )
    except Exception as exc:
        logger.warning("create_notification failed for owner %s: %s", owner_id, exc)

    try:
        send_to_user(
            user_id=owner_id,
            payload={
                "title": notif_title,
                "body": notif_body,
                "data": {"shop_id": event.shop_id, "status": event.new_status},
            },
        )
    except Exception as exc:
        logger.warning("send_to_user push failed for owner %s: %s", owner_id, exc)
