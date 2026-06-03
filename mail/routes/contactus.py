"""Contact us endpoint — stores submissions and notifies admins asynchronously."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from core.config import get_settings
from db.supabase import get_supabase_admin
from mail.queue import enqueue_mail

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/contactus")
async def contact_us(
    full_name: str,
    email: str,
    subject: str,
    message: str,
) -> dict[str, Any]:
    """Submit a contact form message."""
    if not full_name or not email or not subject or not message:
        return {"error": "All fields are required"}

    admin = get_supabase_admin()
    try:
        r = (
            admin.table("contact_submissions")
            .insert({
                "full_name": full_name,
                "email": email,
                "subject": subject,
                "message": message,
            })
            .execute()
        )
    except Exception as exc:
        logger.warning("contact_us insert failed: %s", exc)
        return {"error": "Failed to submit message. Please try again."}

    # Notify admins (best-effort, queued)
    settings = get_settings()
    recipients = settings.admin_notification_recipients
    if recipients:
        inner = f"""
        <p>A new contact form submission has been received.</p>
        <ul>
          <li><strong>Name:</strong> {full_name}</li>
          <li><strong>Email:</strong> {email}</li>
          <li><strong>Subject:</strong> {subject}</li>
        </ul>
        <div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
          <strong>Message:</strong><br/>{message}
        </div>
        """
        for recipient in recipients:
            await enqueue_mail(
                to=recipient,
                subject=f"[Midora Contact] {subject}",
                body_html=f"""
                <div style="font-family:sans-serif;padding:24px;">
                  <h2>New Contact Submission</h2>
                  {inner}
                  <p style="margin-top:24px;">
                    <a href="{settings.frontend_public_url}/admin" style="display:inline-block;padding:10px 18px;background:#0f172a;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">
                      Open admin panel
                    </a>
                  </p>
                </div>
                """,
            )

    return {"status": "submitted", "message": "Thank you! We'll get back to you soon."}
