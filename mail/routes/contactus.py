"""Contact us endpoint — stores submissions and notifies admins asynchronously."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

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

    # Confirmation to the sender
    from mail.send import _html_shell
    confirm_inner = f"""
    <p>Hi {full_name},</p>
    <p>Thank you for reaching out to Midora. We've received your message and will get back to you as soon as possible.</p>
    <div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
      <strong>Your message:</strong><br/>{message}
    </div>
    <p style="margin-top:20px;color:#64748b;font-size:13px;">If you have any urgent concerns, please reply directly to this email.</p>
    """
    await enqueue_mail(
        to=email,
        subject="We received your message — Midora",
        body_html=_html_shell("Message received", confirm_inner),
    )

    # Notify admins (best-effort, queued)
    from mail.queue import get_admin_emails, filter_recipients
    recipients = filter_recipients(get_admin_emails(), email)
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
        body_html = _html_shell("New contact submission", inner)
        for recipient in recipients:
            await enqueue_mail(
                to=recipient,
                subject=f"[Midora Contact] {subject}",
                body_html=body_html,
            )

    return {"status": "submitted", "message": "Thank you! We'll get back to you soon."}
