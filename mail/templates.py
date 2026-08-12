"""MJML-based email template renderer.

We compile ``.mjml`` files → HTML once per process (LRU-cached) using
``mrml`` (a Rust MJML implementation with pure-wheel Python bindings — no
Node.js needed on Vercel). Runtime variables are then injected via
``string.Template`` so CSS braces ``{...}`` in the compiled HTML never
collide with substitution syntax.

Public ``render_*`` functions each return ``(subject, body_html)`` — the
route layer just enqueues that pair. HTML construction never happens in
route handlers.

To add a new template:
  1. Drop ``mail/templates/<name>.mjml`` (use ``$var`` for placeholders).
  2. Add a ``render_<name>(...) -> tuple[str, str]`` here.
  3. Route/service code calls the ``render_*`` function only.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from string import Template

import mrml

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=32)
def _compile_template(name: str) -> Template:
    """Compile ``mail/templates/<name>.mjml`` to a HTML ``string.Template``.

    Cached: MJML → HTML compilation is done once per template per process,
    which is a few milliseconds for the templates we ship but adds up under
    load. On Vercel this cache lives for the duration of a warm container.
    """
    path = _TEMPLATES_DIR / f"{name}.mjml"
    if not path.is_file():
        raise FileNotFoundError(f"MJML template not found: {path}")

    mjml_src = path.read_text(encoding="utf-8")
    output = mrml.to_html(mjml_src)

    for warning in output.warnings or []:
        logger.warning("mrml warning in %s.mjml: %s", name, warning)

    return Template(output.content)


def _render(name: str, /, **variables: object) -> str:
    """Render a compiled template, substituting ``$var`` placeholders."""
    template = _compile_template(name)
    # safe_substitute leaves unknown `$foo` intact rather than raising —
    # protects against email regressions if a caller forgets a var.
    return template.safe_substitute(**variables)


# ---------------------------------------------------------------------------
# Public renderers — each returns (subject, body_html)
# ---------------------------------------------------------------------------


def render_verify_email(*, verification_link: str) -> tuple[str, str]:
    """Account activation email sent right after registration."""
    html = _render("verify_email", verification_link=verification_link)
    return "Verify your Midora email", html


def render_product_submitted_confirmation(*, product_title: str) -> tuple[str, str]:
    """Confirmation to the merchant when a product is submitted for review."""
    html = _render("product_submitted", product_title=product_title)
    return f"Product submitted: {product_title} — Midora", html


def render_new_product_admin_notification(
    *,
    shop_name: str,
    product_title: str,
    price_ugx: float,
    category: str | None,
    admin_listings_url: str,
) -> tuple[str, str]:
    """Admin alert when a new product is waiting for moderation."""
    html = _render(
        "new_product_admin",
        shop_name=shop_name,
        product_title=product_title,
        price_ugx_formatted=f"{price_ugx:,.0f}",
        category=category or "—",
        admin_listings_url=admin_listings_url,
    )
    return f"[Midora] New product: {product_title}", html


def render_shop_verification_decision(
    *,
    shop_name: str,
    decision: str,
    notes: str | None = None,
) -> tuple[str, str]:
    """Approve/reject notification for merchant shop verification.

    ``decision`` is ``"verified"``, ``"rejected"``, or any other custom
    status string. Everything not ``"verified"`` renders the amber
    "action needed" template.
    """
    notes_block = ""
    if notes:
        notes_block = (
            f'<div class="notes-card"><strong>Notes from our team:</strong><br/>{notes}</div>'
        )

    if decision == "verified":
        html = _render(
            "shop_verification_approved",
            shop_name=shop_name,
            notes_block=notes_block,
        )
        subject = f"'{shop_name}' is live on Midora"
    else:
        html = _render(
            "shop_verification_rejected",
            shop_name=shop_name,
            notes_block=notes_block,
        )
        subject = f"Update on your shop '{shop_name}'"

    return subject, html


def render_listing_decision(
    *,
    product_title: str,
    decision: str,
    reason: str | None = None,
    listings_url: str | None = None,
) -> tuple[str, str]:
    """Merchant email for a moderation outcome.

    ``decision`` is ``"approved"``, ``"rejected"``, or ``"needs_review"``.
    ``reason`` is the moderation reason surfaced to the merchant. Substituted
    blocks are plain HTML (not mj-* tags) because substitution runs on the
    already-compiled HTML.
    """
    reason_block = ""
    if reason:
        reason_block = (
            '<div style="background:#f8fafc;border:1px solid #e2e8f0;'
            'border-radius:10px;padding:14px 16px;color:#334155;font-size:14px;'
            'margin-top:4px;"><strong>Reason:</strong><br/>'
            f"{reason}</div>"
        )

    _badge_base = (
        "display:inline-block;padding:4px 12px;border-radius:999px;"
        "font-size:12px;font-weight:600;"
    )
    _btn_style = (
        "display:inline-block;margin-top:18px;padding:12px 22px;"
        "border-radius:10px;background:#0f172a;color:#ffffff;"
        "text-decoration:none;font-weight:600;font-size:14px;"
    )

    cta_block = ""
    if decision == "approved":
        badge_style = _badge_base + "background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;"
        badge_label = "Approved"
        heading = "Your listing is live"
        message = (
            f"Good news — <strong>{product_title}</strong> passed review and is "
            "now visible to buyers on Midora."
        )
        if listings_url:
            cta_block = f'<a style="{_btn_style}" href="{listings_url}">View your listings</a>'
        subject = f"Approved: {product_title} is live on Midora"
        preview = f"{product_title} is now live on Midora"
    elif decision == "needs_review":
        badge_style = _badge_base + "background:#fffbeb;color:#b45309;border:1px solid #fde68a;"
        badge_label = "Under review"
        heading = "Your listing is under review"
        message = (
            f"<strong>{product_title}</strong> needs a closer look from our team "
            "before it goes live. We'll email you again once a decision is made."
        )
        subject = f"Under review: {product_title} — Midora"
        preview = f"{product_title} is under manual review"
    else:  # rejected
        badge_style = _badge_base + "background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;"
        badge_label = "Not approved"
        heading = "Your listing wasn't approved"
        message = (
            f"Unfortunately <strong>{product_title}</strong> didn't meet our "
            "listing policy and hasn't been published. You can edit it and "
            "re-submit from your dashboard."
        )
        if listings_url:
            cta_block = f'<a style="{_btn_style}" href="{listings_url}">Edit and re-submit</a>'
        subject = f"Action needed: {product_title} wasn't approved"
        preview = f"{product_title} wasn't approved"

    html = _render(
        "listing_decision",
        preview=preview,
        badge_style=badge_style,
        badge_label=badge_label,
        heading=heading,
        message=message,
        reason_block=reason_block,
        cta_block=cta_block,
    )
    return subject, html


# ---------------------------------------------------------------------------
# Backwards-compat aliases — Phase B code imports build_* names.
# ---------------------------------------------------------------------------

build_product_submitted_confirmation = render_product_submitted_confirmation
build_new_product_admin_notification = render_new_product_admin_notification
