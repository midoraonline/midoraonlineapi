"""SMTP helpers built on fastapi-mail.

All templates are simple inline HTML (with a plain-text fallback where it
matters). Every send is best-effort — callers catch exceptions so email
failures never break the underlying flow.

All public `send_*` functions now enqueue rather than send synchronously.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from core.config import get_settings

logger = logging.getLogger(__name__)

_conf: FastMail | None = None


def get_mail() -> FastMail:
    global _conf
    if _conf is None:
        settings = get_settings()
        config = ConnectionConfig(
            MAIL_USERNAME=settings.email,
            MAIL_PASSWORD=settings.email_password,
            MAIL_FROM=settings.email,
            MAIL_FROM_NAME="Midora",
            MAIL_PORT=587,
            MAIL_SERVER="smtp.gmail.com",
            MAIL_STARTTLS=True,
            MAIL_SSL_TLS=False,
        )
        _conf = FastMail(config)
    return _conf


# ---------------------------------------------------------------------------
# Shared HTML shell (exported for use by queue worker)
# ---------------------------------------------------------------------------


_LOGO_CID = "midora_logo"
_LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "logo.png")


def _html_shell(title: str, inner: str) -> str:
    """Wrap email body HTML in a minimal, email-client-safe shell."""
    return f"""
    <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f6f7f9;padding:32px 16px;\">
      <div style=\"max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;\">
        <div style=\"padding:24px 28px;border-bottom:1px solid #f1f5f9;text-align:center;\">
          <img src=\"cid:{_LOGO_CID}\" alt=\"Midora\" style=\"max-width:140px;height:auto;\" />
          <div style=\"margin-top:8px;font-size:18px;font-weight:600;color:#0f172a;\">{title}</div>
        </div>
        <div style=\"padding:24px 28px;font-size:15px;line-height:1.55;color:#1f2937;\">
          {inner}
        </div>
        <div style=\"padding:20px 28px;border-top:1px solid #f1f5f9;font-size:12px;color:#64748b;\">
          You're receiving this message from the Midora team. If you have
          questions, simply reply to this email.
        </div>
      </div>
    </div>
    """.strip()


async def _send_html(
    *,
    to: Iterable[str] | str,
    subject: str,
    body_html: str,
) -> None:
    """Low-level send — used by queue worker, not by callers directly."""
    recipients = [to] if isinstance(to, str) else [r for r in to if r]
    if not recipients:
        return
    mail = get_mail()
    message = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=body_html,
        subtype=MessageType.html,
        attachments=[
            {
                "file": _LOGO_PATH,
                "headers": {
                    "Content-ID": f"<{_LOGO_CID}>",
                    "Content-Disposition": "inline; filename=\"logo.png\"",
                },
                "mime_type": "image",
                "mime_subtype": "png",
            }
        ],
    )
    await mail.send_message(message)


# ---------------------------------------------------------------------------
# Account lifecycle
# ---------------------------------------------------------------------------


async def _enqueue(to: str, subject: str, body_html: str) -> None:
    from mail.queue import enqueue_mail
    await enqueue_mail(to=to, subject=subject, body_html=body_html)

async def send_verification_email(to: str, verification_link: str) -> None:
    body = _html_shell(
        "Verify your email",
        f"""
        <p>Thanks for joining Midora. Please confirm your email address to activate your account.</p>
        <p style=\"margin:24px 0;\">
          <a href=\"{verification_link}\" style=\"display:inline-block;padding:12px 20px;background:#0f172a;color:#ffffff;text-decoration:none;border-radius:10px;font-weight:600;\">Verify email</a>
        </p>
        <p style=\"color:#64748b;font-size:13px;\">If the button doesn't work, paste this link into your browser:<br/>
          <span style=\"word-break:break-all;color:#334155;\">{verification_link}</span>
        </p>
        """,
    )
    await _enqueue(to=to, subject="Verify your Midora email", body_html=body)


# ---------------------------------------------------------------------------
# Shop verification lifecycle
# ---------------------------------------------------------------------------


async def send_shop_verification_decision_email(
    to: str, shop_name: str, decision: str, notes: str | None = None
) -> None:
    """Notify a merchant that their shop verification was approved or rejected."""
    notes_html = (
        f"<div style=\"margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;color:#334155;\"><strong>Notes from our team:</strong><br/>{notes}</div>"
        if notes
        else ""
    )

    if decision == "verified":
        subject = f"'{shop_name}' is live on Midora"
        inner = f"""
          <p>Great news — <strong>{shop_name}</strong> has been verified and is now visible to customers on Midora.</p>
          <p style=\"margin:20px 0;\">You can start adding products, configuring your storefront, and sharing your shop link.</p>
          {notes_html}
        """
        title = "Your shop is verified"
    elif decision == "rejected":
        subject = f"Update on your shop '{shop_name}'"
        inner = f"""
          <p>We've reviewed <strong>{shop_name}</strong> and unfortunately could not verify it at this time.</p>
          <p>You can update your details and re-submit for verification from your merchant dashboard.</p>
          {notes_html}
        """
        title = "Verification update"
    else:
        subject = f"Update on your shop '{shop_name}'"
        inner = f"""
          <p>The verification status of <strong>{shop_name}</strong> is now: <code>{decision}</code>.</p>
          {notes_html}
        """
        title = "Verification update"

    await _enqueue(to, subject, _html_shell(title, inner))


async def send_shop_submission_received_email(
    to: str, shop_name: str
) -> None:
    """Confirmation to a merchant that we've received their verification request."""
    inner = f"""
      <p>We've received your verification request for <strong>{shop_name}</strong>.</p>
      <p>Our team typically reviews new shops within one business day. We'll email you as soon as a decision is made.</p>
      <p style=\"color:#64748b;font-size:13px;\">In the meantime, you can keep polishing your storefront from the merchant dashboard.</p>
    """
    await _enqueue(to, "We received your shop verification request", _html_shell("Verification request received", inner))


async def send_new_shop_submission_admin_email(
    *,
    admin_recipients: Iterable[str],
    shop_name: str,
    shop_slug: str | None,
    shop_id: str,
    merchant_email: str | None,
    notes: str | None = None,
) -> None:
    """Internal notification to the Midora team when a shop is submitted for review."""
    recipients = [r for r in admin_recipients if r]
    if not recipients:
        return

    merchant_html = (
        f"<li><strong>Merchant:</strong> {merchant_email}</li>" if merchant_email else ""
    )
    slug_html = (
        f"<li><strong>Slug:</strong> {shop_slug}</li>" if shop_slug else ""
    )
    notes_html = (
        f"<div style=\"margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;color:#334155;\"><strong>Merchant notes:</strong><br/>{notes}</div>"
        if notes
        else ""
    )

    inner = f"""
      <p>A new shop has just been submitted for verification.</p>
      <ul style=\"padding-left:20px;color:#1f2937;\">
        <li><strong>Shop:</strong> {shop_name}</li>
        {slug_html}
        <li><strong>Shop ID:</strong> {shop_id}</li>
        {merchant_html}
      </ul>
      {notes_html}
      <p style=\"margin-top:24px;\">Head to the admin dashboard to approve or reject it.</p>
    """
    for recipient in recipients:
        await _enqueue(recipient, f"[Midora] New shop submission: {shop_name}", _html_shell("New shop awaiting verification", inner))
