"""Notify the Next.js app to bust Vercel Data Cache / unstable_cache entries.

Requires FRONTEND_PUBLIC_URL (or NEXT_PUBLIC_SITE_URL) and REVALIDATE_SECRET to
match the values on the frontend deployment (see midora/app/api/revalidate/route.ts).
If unset, calls are skipped — safe for local API-only dev.
"""

from __future__ import annotations

import logging

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

VALID_TAGS = frozenset({"shops", "products", "most-viewed"})


def revalidate_nextjs_cache_tag(tag: str) -> None:
    if tag not in VALID_TAGS:
        logger.warning("revalidate_nextjs_cache_tag: unknown tag %r (skipped)", tag)
        return
    settings = get_settings()
    base = (settings.frontend_public_url or "").strip().rstrip("/")
    secret = (settings.revalidate_secret or "").strip()
    if not base or not secret:
        return
    url = f"{base}/api/revalidate"
    try:
        res = httpx.post(
            url,
            params={"secret": secret, "tag": tag},
            timeout=15.0,
        )
        if res.status_code >= 400:
            logger.warning(
                "Next.js revalidate POST failed: %s %s",
                res.status_code,
                (res.text or "")[:500],
            )
    except httpx.HTTPError as exc:
        logger.warning("Next.js revalidate HTTP error: %s", exc)
