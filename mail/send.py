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
    from mail.templates import render_verify_email

    subject, body = render_verify_email(verification_link=verification_link)
    await _enqueue(to=to, subject=subject, body_html=body)


# ---------------------------------------------------------------------------
# Shop verification lifecycle
# ---------------------------------------------------------------------------


async def send_shop_verification_decision_email(
    to: str, shop_name: str, decision: str, notes: str | None = None
) -> None:
    """Notify a merchant that their shop verification was approved or rejected."""
    from mail.templates import render_shop_verification_decision

    subject, body = render_shop_verification_decision(
        shop_name=shop_name, decision=decision, notes=notes,
    )
    await _enqueue(to, subject, body)


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


async def send_shop_opened_merchant_email(
    to: str, shop_name: str, shop_id: str, verification_url: str
) -> None:
    """Welcome a new merchant and guide them to the verification journey."""
    inner = f"""
      <p>Congratulations — <strong>{shop_name}</strong> is now on Midora! 🎉</p>
      <p>Your shop has automatically earned the <strong>Shop Listed</strong> badge. Here's what to do next:</p>
      <ol style="padding-left:20px;color:#1f2937;line-height:1.8;">
        <li><strong>Complete your identity verification</strong> — upload your NIN/ID documents so customers can trust you.</li>
        <li><strong>Add your first product or service</strong> — start listing what you sell.</li>
        <li><strong>Verify your business</strong> — share your physical shop details for the highest trust badge.</li>
      </ol>
      <p style="margin:24px 0;">
        <a href="{verification_url}" style="display:inline-block;padding:12px 20px;background:#D4653C;color:#ffffff;text-decoration:none;border-radius:10px;font-weight:600;">Start verification →</a>
      </p>
      <p style="color:#64748b;font-size:13px;">Each verification stage unlocks a new trust badge that shows customers your shop is legitimate.</p>
    """
    await _enqueue(to, f"Welcome to Midora — {shop_name} is live!", _html_shell("Your shop is live 🎉", inner))


async def send_shop_opened_admin_email(
    *,
    admin_recipients: Iterable[str],
    shop_name: str,
    shop_id: str,
    merchant_email: str | None,
) -> None:
    """Internal notification to the Midora team when a new shop is created."""
    from mail.queue import filter_recipients

    recipients = filter_recipients(admin_recipients, merchant_email)
    if not recipients:
        return
    merchant_html = (
        f"<li><strong>Merchant:</strong> {merchant_email}</li>" if merchant_email else ""
    )
    inner = f"""
      <p>A new shop has just been opened on Midora.</p>
      <ul style="padding-left:20px;color:#1f2937;">
        <li><strong>Shop:</strong> {shop_name}</li>
        <li><strong>Shop ID:</strong> {shop_id}</li>
        {merchant_html}
      </ul>
      <p style="margin-top:24px;">The merchant has been guided to the verification flow. You may review the shop in the admin dashboard.</p>
    """
    for recipient in recipients:
        await _enqueue(recipient, f"[Midora] New shop opened: {shop_name}", _html_shell("New shop opened", inner))


async def send_stage_submission_merchant_email(
    to: str, shop_name: str, stage: int
) -> None:
    """Confirm to the merchant that their stage submission was received."""
    stage_names = {2: "Identity Verification", 3: "Business Verification"}
    stage_name = stage_names.get(stage, f"Stage {stage}")
    inner = f"""
      <p>We've received your <strong>{stage_name}</strong> submission for <strong>{shop_name}</strong>.</p>
      <p>Our team typically reviews submissions within one business day. We'll email you as soon as a decision is made.</p>
      <p style="color:#64748b;font-size:13px;">In the meantime, you can keep adding products to your shop.</p>
    """
    await _enqueue(to, f"Verification submission received — {shop_name}", _html_shell(f"{stage_name} received", inner))


async def send_stage_submission_admin_email(
    *,
    admin_recipients: Iterable[str],
    shop_name: str,
    shop_id: str,
    stage: int,
    merchant_email: str | None,
) -> None:
    """Internal notification when a merchant submits a verification stage."""
    from mail.queue import filter_recipients

    recipients = filter_recipients(admin_recipients, merchant_email)
    if not recipients:
        return
    stage_names = {2: "Identity Verification", 3: "Business Verification"}
    stage_name = stage_names.get(stage, f"Stage {stage}")
    merchant_html = (
        f"<li><strong>Merchant:</strong> {merchant_email}</li>" if merchant_email else ""
    )
    inner = f"""
      <p>A merchant has submitted <strong>{stage_name}</strong> for review.</p>
      <ul style="padding-left:20px;color:#1f2937;">
        <li><strong>Shop:</strong> {shop_name}</li>
        <li><strong>Shop ID:</strong> {shop_id}</li>
        <li><strong>Stage:</strong> {stage_name}</li>
        {merchant_html}
      </ul>
      <p style="margin-top:24px;">Head to the admin dashboard to approve or reject this submission.</p>
    """
    for recipient in recipients:
        await _enqueue(recipient, f"[Midora] {stage_name} submission: {shop_name}", _html_shell(f"New {stage_name} submission", inner))


async def send_stage_approved_email(
    to: str, shop_name: str, stage: int
) -> None:
    """Notify the merchant that a verification stage was approved and they earned a badge."""
    badge_names = {2: ("Identity Verified", "🪪"), 3: ("Business Verified", "🏢")}
    badge_name, emoji = badge_names.get(stage, (f"Stage {stage} Verified", "✅"))
    next_step = ""
    if stage == 2:
        next_step = "<p>You can now proceed to <strong>Business Verification</strong> (Stage 3) to earn the highest trust badge on Midora.</p>"
    inner = f"""
      <p>Great news — your <strong>{badge_name}</strong> {emoji} badge has been approved for <strong>{shop_name}</strong>!</p>
      <p>This badge now appears on your public shop page, helping customers trust your shop.</p>
      {next_step}
      <p style="color:#64748b;font-size:13px;">Thank you for helping make Midora a trusted marketplace.</p>
    """
    await _enqueue(to, f"{emoji} {badge_name} badge earned — {shop_name}", _html_shell(f"{badge_name} badge earned!", inner))


async def send_stage_rejected_email(
    to: str, shop_name: str, stage: int, notes: str | None = None
) -> None:
    """Notify the merchant that a verification stage was rejected with reviewer notes."""
    stage_names = {2: "Identity Verification", 3: "Business Verification"}
    stage_name = stage_names.get(stage, f"Stage {stage}")
    notes_html = (
        f'<div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;color:#334155;"><strong>Notes from our team:</strong><br/>{notes}</div>'
        if notes else ""
    )
    inner = f"""
      <p>We've reviewed your <strong>{stage_name}</strong> submission for <strong>{shop_name}</strong> and need some changes before we can approve it.</p>
      {notes_html}
      <p>Please address the feedback above and resubmit from your merchant dashboard.</p>
    """
    await _enqueue(to, f"Update on your {stage_name} — {shop_name}", _html_shell("Verification update", inner))


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
    from mail.queue import filter_recipients

    recipients = filter_recipients(admin_recipients, merchant_email)
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

async def send_feedback_admin_email(
    *,
    admin_recipients: Iterable[str],
    user_email: str | None,
    feedback_text: str,
) -> None:
    """Internal notification when a user submits feedback."""
    from mail.queue import filter_recipients

    recipients = filter_recipients(admin_recipients, user_email)
    if not recipients:
        return
    user_html = (
        f"<li><strong>User:</strong> {user_email}</li>" if user_email else "<li><strong>User:</strong> Anonymous</li>"
    )
    inner = f"""
      <p>A user has submitted feedback on the platform.</p>
      <ul style="padding-left:20px;color:#1f2937;">
        {user_html}
      </ul>
      <div style="margin-top:20px;padding:14px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;color:#334155;">
        <strong>Feedback:</strong><br/>
        {feedback_text}
      </div>
    """
    for recipient in recipients:
        await _enqueue(recipient, "[Midora] New User Feedback Received", _html_shell("New User Feedback", inner))

