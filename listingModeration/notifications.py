"""Merchant email notifications for moderation decisions.

Best-effort: never raises into the pipeline or a route. Resolves the
seller's email and enqueues a decision email (approved / rejected /
needs_review). Used by both the auto-pipeline and admin manual review.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from core.config import get_settings
from db.supabase import get_supabase_admin
from mail.queue import enqueue_mail
from mail.templates import render_listing_decision

from .config import config
from .schemas import ModerationDecision, ModerationRow, ModerationStatus

logger = logging.getLogger(__name__)

_STATUS_TO_LABEL = {
    ModerationStatus.APPROVED: "approved",
    ModerationStatus.REJECTED: "rejected",
    ModerationStatus.NEEDS_REVIEW: "needs_review",
}

# Maps products.status (admin path) onto the email decision labels.
_PRODUCT_STATUS_TO_LABEL = {
    "active": "approved",
    "rejected": "rejected",
    "pending_review": "needs_review",
}


def _email_from_seller_id(seller_id: Any) -> Optional[str]:
    if not seller_id:
        return None
    try:
        admin = get_supabase_admin()
        r = (
            admin.table("users")
            .select("email")
            .eq("id", str(seller_id))
            .limit(1)
            .execute()
        )
        if r.data and r.data[0].get("email"):
            return str(r.data[0]["email"])
    except Exception as exc:
        logger.warning("seller email lookup failed for %s: %s", seller_id, exc)
    return None


def _email_from_product(product_id: Any, shop_id: Any = None) -> Optional[str]:
    """Resolve merchant email via product -> shop.owner_id -> users.email."""
    admin = get_supabase_admin()
    try:
        if not shop_id and product_id:
            pr = (
                admin.table("products")
                .select("shop_id")
                .eq("id", str(product_id))
                .limit(1)
                .execute()
            )
            shop_id = pr.data[0]["shop_id"] if pr.data else None
        if not shop_id:
            return None
        sr = (
            admin.table("shops")
            .select("owner_id")
            .eq("id", str(shop_id))
            .limit(1)
            .execute()
        )
        owner_id = sr.data[0]["owner_id"] if sr.data else None
        return _email_from_seller_id(owner_id)
    except Exception as exc:
        logger.warning("owner email lookup failed for product %s: %s", product_id, exc)
        return None


def _listings_url() -> Optional[str]:
    settings = get_settings()
    base = getattr(settings, "frontend_public_url", "") or ""
    return f"{base.rstrip('/')}/merchant" if base else None


async def _send(email: str, product_title: str, label: str, reason: Optional[str]) -> None:
    subject, html = render_listing_decision(
        product_title=product_title,
        decision=label,
        reason=reason,
        listings_url=_listings_url(),
    )
    try:
        await enqueue_mail(to=email, subject=subject, body_html=html)
    except Exception as exc:
        logger.warning("enqueue decision email failed for %s: %s", email, exc)


async def notify_decision(row: ModerationRow, decision: ModerationDecision) -> None:
    """Auto-pipeline path: email the merchant of a queue-row decision."""
    if not config.notify_merchant_on_decision:
        return
    label = _STATUS_TO_LABEL.get(decision.status)
    if label is None:
        return  # failed / processing — no email
    if label == "approved" and not config.notify_merchant_on_approved:
        return
    email = _email_from_seller_id(row.seller_id) or _email_from_product(row.product_id)
    if not email:
        logger.info("no seller email for moderation row %s; skipping email", row.id)
        return
    await _send(email, row.title, label, decision.reason)


async def notify_product_manual(
    product_row: dict[str, Any],
    new_status: str,
    reason: Optional[str],
) -> None:
    """Admin path: email the merchant when a listing is manually decided."""
    if not config.notify_merchant_on_decision:
        return
    label = _PRODUCT_STATUS_TO_LABEL.get(new_status)
    if label is None:
        return
    if label == "approved" and not config.notify_merchant_on_approved:
        return
    email = _email_from_product(product_row.get("id"), product_row.get("shop_id"))
    if not email:
        logger.info(
            "no seller email for product %s; skipping decision email",
            product_row.get("id"),
        )
        return
    title = str(product_row.get("title") or "Your listing")
    await _send(email, title, label, reason)
