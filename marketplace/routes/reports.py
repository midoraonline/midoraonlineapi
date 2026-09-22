from __future__ import annotations

import html
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

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
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Must be one of: {', '.join(REPORT_REASONS)}",
        )
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

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

            reporter_email: str | None = None
            product_title = product_id
            try:
                pr = admin.table("products").select("title").eq("id", product_id).limit(1).execute()
                if pr.data:
                    product_title = pr.data[0].get("title", product_id)
            except Exception:
                pass

            # Confirmation to the reporter
            try:
                from mail.send import _html_shell

                reporter_r = admin.table("users").select("email").eq("id", current_user_id).limit(1).execute()
                if reporter_r.data and reporter_r.data[0].get("email"):
                    reporter_email = reporter_r.data[0]["email"]
                    confirm_inner = f"""
                    <p>Thank you for letting us know. We've received your report regarding <strong>{html.escape(str(product_title))}</strong>.</p>
                    <p>Our team will review it and take appropriate action. We appreciate your help keeping Midora safe.</p>
                    """
                    await enqueue_mail(
                        to=reporter_email,
                        subject="Report received — Midora",
                        body_html=_html_shell("Report received", confirm_inner),
                    )
            except Exception:
                pass

            # Notify admins (best-effort, queued)
            try:
                from mail.send import _html_shell
                from mail.queue import get_admin_emails, filter_recipients

                recipients = filter_recipients(get_admin_emails(), reporter_email)
                if recipients:
                    settings = get_settings()
                    inner = f"""
                    <p>A product has been reported:</p>
                    <ul>
                      <li><strong>Product:</strong> {html.escape(str(product_title))}</li>
                      <li><strong>Reason:</strong> {html.escape(str(reason))}</li>
                      <li><strong>Reporter:</strong> {html.escape(str(current_user_id))}</li>
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
        raise HTTPException(status_code=500, detail="Failed to submit report") from exc


SELLER_REPORT_REASONS = [
    "Scam or fraud",
    "Harassment or abuse",
    "Fake identity",
    "Spam",
    "Unresponsive after deal",
    "Other",
]


@router.post("/sellers/{seller_id}/reports")
async def report_seller(
    seller_id: str,
    reason: str,
    description: str | None = None,
    shop_id: str | None = None,
    current_user_id: str = Depends(get_optional_user_id),
) -> dict[str, Any]:
    """Report a seller (thin trust path for admins)."""
    if reason not in SELLER_REPORT_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Must be one of: {', '.join(SELLER_REPORT_REASONS)}",
        )
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user_id == seller_id:
        raise HTTPException(status_code=400, detail="You cannot report yourself")

    admin = get_supabase_admin()
    try:
        existing = (
            admin.table("seller_reports")
            .select("id")
            .eq("seller_id", seller_id)
            .eq("reporter_id", current_user_id)
            .eq("resolved", False)
            .limit(1)
            .execute()
        )
        if existing.data:
            return {"status": "already_reported", "message": "You have already reported this seller"}

        payload = {
            "seller_id": seller_id,
            "reporter_id": current_user_id,
            "reason": reason,
            "description": description,
        }
        if shop_id:
            payload["shop_id"] = shop_id
        r = admin.table("seller_reports").insert(payload).execute()
        return r.data[0] if r.data else {"status": "reported"}
    except Exception as exc:
        logger.warning("report_seller failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to submit seller report") from exc


@router.post("/sellers/{seller_id}/block")
async def block_seller(
    seller_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Buyer-side block: hide seller / stop contact for this account."""
    if current_user_id == seller_id:
        raise HTTPException(status_code=400, detail="You cannot block yourself")
    admin = get_supabase_admin()
    try:
        existing = (
            admin.table("seller_blocks")
            .select("id")
            .eq("blocker_id", current_user_id)
            .eq("seller_id", seller_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            return {"status": "already_blocked", "blocked": True}
        admin.table("seller_blocks").insert({
            "blocker_id": current_user_id,
            "seller_id": seller_id,
        }).execute()
        return {"status": "blocked", "blocked": True}
    except Exception as exc:
        logger.warning("block_seller failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to block seller") from exc


@router.delete("/sellers/{seller_id}/block")
async def unblock_seller(
    seller_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        admin.table("seller_blocks").delete().eq("blocker_id", current_user_id).eq("seller_id", seller_id).execute()
        return {"status": "unblocked", "blocked": False}
    except Exception as exc:
        logger.warning("unblock_seller failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to unblock seller") from exc


@router.get("/me/blocked-sellers")
async def list_blocked_sellers(
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("seller_blocks")
            .select("seller_id,created_at")
            .eq("blocker_id", current_user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return {"items": r.data or []}
    except Exception as exc:
        logger.warning("list_blocked_sellers failed: %s", exc)
        return {"items": []}
