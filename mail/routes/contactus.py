"""Contact us endpoint — stores submissions and notifies admins asynchronously."""

from __future__ import annotations

import html
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from db.supabase import get_supabase_admin
from mail.queue import enqueue_mail

logger = logging.getLogger(__name__)

router = APIRouter()


class ContactRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


@router.post("/contactus")
async def contact_us(body: ContactRequest) -> dict[str, Any]:
    """Submit a contact form message."""
    admin = get_supabase_admin()
    try:
        (
            admin.table("contact_submissions")
            .insert({
                "full_name": body.full_name,
                "email": str(body.email),
                "subject": body.subject,
                "message": body.message,
            })
            .execute()
        )
    except Exception as exc:
        logger.warning("contact_us insert failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to submit message. Please try again.",
        ) from exc

    safe_name = html.escape(body.full_name)
    safe_email = html.escape(str(body.email))
    safe_subject = html.escape(body.subject)
    safe_message = html.escape(body.message).replace("\n", "<br/>")

    from mail.send import _html_shell
    confirm_inner = f"""
    <p>Hi {safe_name},</p>
    <p>Thank you for reaching out to Midora. We've received your message and will get back to you as soon as possible.</p>
    <div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
      <strong>Your message:</strong><br/>{safe_message}
    </div>
    <p style="margin-top:20px;color:#64748b;font-size:13px;">If you have any urgent concerns, please reply directly to this email.</p>
    """
    await enqueue_mail(
        to=str(body.email),
        subject="We received your message — Midora",
        body_html=_html_shell("Message received", confirm_inner),
    )

    from mail.queue import get_admin_emails, filter_recipients
    recipients = filter_recipients(get_admin_emails(), str(body.email))
    if recipients:
        inner = f"""
        <p>A new contact form submission has been received.</p>
        <ul>
          <li><strong>Name:</strong> {safe_name}</li>
          <li><strong>Email:</strong> {safe_email}</li>
          <li><strong>Subject:</strong> {safe_subject}</li>
        </ul>
        <div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
          <strong>Message:</strong><br/>{safe_message}
        </div>
        """
        body_html = _html_shell("New contact submission", inner)
        for recipient in recipients:
            await enqueue_mail(
                to=recipient,
                subject=f"[Midora Contact] {safe_subject}",
                body_html=body_html,
            )

    return {"status": "submitted", "message": "Thank you! We'll get back to you soon."}
