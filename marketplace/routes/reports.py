from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from core.config import get_settings
from core.security import get_current_user_id, get_optional_user_id
from db.supabase import get_supabase_admin
from mail.queue import enqueue_mail

logger = logging.getLogger(__name__)

router = APIRouter()


REPORT_REASONS = [
    "Fake or counterfeit item",
    "Scam or fraud",
    "Prohibited item",
    "Wrong category",
    "Spam or duplicate",
    "Misleading description",
    "Other",
]


@router.post("/products/{product_id}/reports")
async def report_product(
    product_id: str,
    reason: str,
    description: str | None = None,
    current_user_id: str = Depends(get_optional_user_id),
) -> dict[str, Any]:
    """Report a product listing."""
    if reason not in REPORT_REASONS:
        return {"error": f"Invalid reason. Must be one of: {', '.join(REPORT_REASONS)}"}
    if not current_user_id:
        return {"error": "Authentication required"}

    admin = get_supabase_admin()
    try:
        existing = (
            admin.table("product_reports")
            .select("id")
            .eq("product_id", product_id)
            .eq("reporter_id", current_user_id)
            .eq("resolved", False)
            .execute()
        )
        if existing.data:
            return {"status": "already_reported", "message": "You have already reported this listing"}

        # Insert into product_reports (admin reports page queries this)
        r = admin.table("product_reports").insert({
            "product_id": product_id,
            "reporter_id": current_user_id,
            "reason": reason,
            "description": description,
        }).execute()

        if r.data:
            # Also record a listing_event so stats/reports_count works
            try:
                seller_id = None
                sr = admin.table("products").select("shop_id").eq("id", product_id).limit(1).execute()
                if sr.data:
                    shop_id = sr.data[0].get("shop_id")
                    if shop_id:
                        srr = admin.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
                        if srr.data:
                            seller_id = srr.data[0].get("owner_id")

                admin.table("listing_events").insert({
                    "listing_id": product_id,
                    "seller_id": seller_id,
                    "buyer_id": current_user_id,
                    "event_type": "reported",
                    "metadata": {"reason": reason, "description": description},
                }).execute()
            except Exception as exc:
                logger.warning("Failed to record listing_event for report: %s", exc)

            # Recalculate listing score
            from ranking.service import calculate_listing_score
            calculate_listing_score(product_id)

            # Confirmation to the reporter
            try:
                from mail.send import _html_shell
                from mail.queue import enqueue_mail
                product_title = r.data[0].get("product_id", product_id)
                try:
                    pr = admin.table("products").select("title").eq("id", product_id).limit(1).execute()
                    if pr.data:
                        product_title = pr.data[0].get("title", product_id)
                except Exception:
                    pass
                reporter_r = admin.table("users").select("email").eq("id", current_user_id).limit(1).execute()
                if reporter_r.data and reporter_r.data[0].get("email"):
                    confirm_inner = f"""
                    <p>Thank you for letting us know. We've received your report regarding <strong>{product_title}</strong>.</p>
                    <p>Our team will review it and take appropriate action. We appreciate your help keeping Midora safe.</p>
                    """
                    await enqueue_mail(
                        to=reporter_r.data[0]["email"],
                        subject="Report received — Midora",
                        body_html=_html_shell("Report received", confirm_inner),
                    )
            except Exception:
                pass

            # Notify admins (best-effort, queued)
            try:
                from mail.send import _html_shell
                from mail.queue import get_admin_emails
                recipients = get_admin_emails()
                if recipients:
                    product_title = r.data[0].get("product_id", product_id)
                    try:
                        pr = admin.table("products").select("title").eq("id", product_id).limit(1).execute()
                        if pr.data:
                            product_title = pr.data[0].get("title", product_id)
                    except Exception:
                        pass

                    settings = get_settings()
                    inner = f"""
                    <p>A product has been reported:</p>
                    <ul>
                      <li><strong>Product:</strong> {product_title}</li>
                      <li><strong>Reason:</strong> {reason}</li>
                      <li><strong>Reporter:</strong> {current_user_id}</li>
                    </ul>
                    <p style="margin-top:24px;">
                      <a href="{settings.frontend_public_url}/admin/reports" style="display:inline-block;padding:10px 18px;background:#0f172a;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:600;">View in admin panel</a>
                    </p>
                    """
                    body_html = _html_shell("Product reported", inner)
                    for recipient in recipients:
                        await enqueue_mail(
                            to=recipient,
                            subject=f"[Midora] Report: {product_title}",
                            body_html=body_html,
                        )
            except Exception as exc:
                logger.warning("Failed to send report notification: %s", exc)

        return r.data[0] if r.data else {"status": "reported"}
    except Exception as exc:
        logger.warning("report_product failed: %s", exc)
        return {"error": "Failed to submit report"}
