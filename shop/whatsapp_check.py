"""Is this number on WhatsApp? Cache first, then the wa-checker worker (whatsapp-web.js).

The worker enforces the ban-safety limits; this module only decides whether to ask it. Anything
other than a definite yes/no is `None` (unknown): the number stays hidden and a later check retries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from core.config import get_settings
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

TTL_REGISTERED = timedelta(days=30)
TTL_NOT_REGISTERED = timedelta(days=3)


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cached_result(e164: str) -> bool | None:
    """A fresh cached answer, or None. Cache errors never block a check."""
    try:
        rows = (
            get_supabase_admin()
            .table("whatsapp_lookup_cache")
            .select("registered,checked_at")
            .eq("e164", e164)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("whatsapp cache read skipped: %s", exc)
        return None
    if not rows:
        return None
    checked = _parse_ts(rows[0].get("checked_at"))
    registered = bool(rows[0].get("registered"))
    ttl = TTL_REGISTERED if registered else TTL_NOT_REGISTERED
    if checked is None or datetime.now(timezone.utc) - checked > ttl:
        return None
    return registered


def _store(e164: str, registered: bool, source: str) -> None:
    try:
        get_supabase_admin().table("whatsapp_lookup_cache").upsert(
            {
                "e164": e164,
                "registered": registered,
                "source": source,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.info("whatsapp cache write skipped: %s", exc)


def checker_enabled() -> bool:
    settings = get_settings()
    return bool(settings.wa_checker_url.strip() and settings.wa_checker_token.strip())


def ask_worker(e164: str) -> bool | None:
    """True/False from the worker, None when it cannot answer (down, capped, circuit open)."""
    if not checker_enabled():
        return None
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.wa_checker_url.rstrip('/')}/check",
            json={"number": e164},
            headers={"Authorization": f"Bearer {settings.wa_checker_token}"},
            timeout=settings.wa_checker_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        logger.warning("wa-checker unreachable: %s", exc)
        return None
    if response.status_code != 200:
        logger.info("wa-checker declined: %s %s", response.status_code, response.text[:200])
        return None
    registered = response.json().get("registered")
    return registered if isinstance(registered, bool) else None


def lookup(e164: str) -> tuple[bool | None, str]:
    """(registered, source). source: cache | library | unavailable."""
    cached = cached_result(e164)
    if cached is not None:
        return cached, "cache"
    live = ask_worker(e164)
    if live is None:
        return None, "unavailable"
    _store(e164, live, "library")
    return live, "library"


def record_on_shop(shop_id: str, registered: bool | None, source: str) -> None:
    """Persist the outcome. Unknown (None) leaves the shop untouched so a later check can settle it."""
    if registered is None:
        return
    get_supabase_admin().table("shops").update(
        {
            "whatsapp_registered": registered,
            "whatsapp_checked_at": datetime.now(timezone.utc).isoformat(),
            "whatsapp_check_source": "library",
        }
    ).eq("id", shop_id).execute()
