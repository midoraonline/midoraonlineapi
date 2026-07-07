"""Async email queue backed by Postgres.

All email sends go through `enqueue_mail()` which inserts into `mail_queue`.
A background worker claims rows atomically via `claim_next_mail_queue_item`
so multiple API processes cannot send the same email twice.

Previously the worker used an in-memory asyncio queue *and* reloaded pending
rows from the DB on startup — with more than one API worker, every process
would claim and send the same pending emails, producing exact duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task[Any] | None = None
_wake_event: asyncio.Event | None = None
POLL_INTERVAL_SECONDS = 2
DEDUP_WINDOW_SECONDS = 120
_MAX_BACKOFF_SECONDS = 60

_db_backoff_seconds = POLL_INTERVAL_SECONDS
_last_db_error_logged_at = 0.0


def _is_transient_network_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if any(
        needle in text
        for needle in (
            "name resolution",
            "temporary failure",
            "connection refused",
            "connection reset",
            "network is unreachable",
            "timed out",
            "could not resolve",
            "getaddrinfo failed",
        )
    ):
        return True
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, OSError) and cause.errno in (-2, -3, 110, 111, 113):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _note_db_success() -> None:
    global _db_backoff_seconds
    _db_backoff_seconds = POLL_INTERVAL_SECONDS


def _note_db_failure(exc: BaseException) -> int:
    """Increase backoff and log at most once per 30s for transient outages."""
    global _db_backoff_seconds, _last_db_error_logged_at
    import time

    if _db_backoff_seconds <= POLL_INTERVAL_SECONDS:
        _db_backoff_seconds = 10
    else:
        _db_backoff_seconds = min(_db_backoff_seconds * 2, _MAX_BACKOFF_SECONDS)

    now = time.monotonic()
    if now - _last_db_error_logged_at >= 30:
        logger.warning(
            "Mail worker: database unreachable (%s); retrying in %ss",
            exc,
            _db_backoff_seconds,
        )
        _last_db_error_logged_at = now
    return _db_backoff_seconds


def _current_poll_interval() -> float:
    return float(_db_backoff_seconds)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def filter_recipients(
    recipients: Iterable[str],
    *exclude: str | None,
) -> list[str]:
    """Drop blank/duplicate addresses and any excluded emails (case-insensitive)."""
    blocked = {e.strip().lower() for e in exclude if e and e.strip()}
    seen: set[str] = set()
    out: list[str] = []
    for raw in recipients:
        email = (raw or "").strip()
        if not email:
            continue
        key = email.lower()
        if key in blocked or key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def get_admin_emails() -> list[str]:
    """Query all users with admin user_role and return their email addresses."""
    try:
        admin = get_supabase_admin()
        r = (
            admin.table("users")
            .select("email")
            .eq("user_role", "admin")
            .execute()
        )
        return filter_recipients(str(row["email"]) for row in (r.data or []) if row.get("email"))
    except Exception as exc:
        logger.warning("Failed to fetch admin emails: %s", exc)
        return []


def _recent_duplicate(recipient: str, subject: str) -> bool:
    """Skip enqueue when the same email was queued or sent very recently."""
    try:
        admin = get_supabase_admin()
        since = (datetime.now(timezone.utc) - timedelta(seconds=DEDUP_WINDOW_SECONDS)).isoformat()
        r = (
            admin.table("mail_queue")
            .select("id")
            .eq("recipient", recipient)
            .eq("subject", subject)
            .gte("created_at", since)
            .in_("status", ["pending", "processing", "sent"])
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception as exc:
        logger.warning("mail dedup check failed: %s", exc)
        return False


def _signal_worker() -> None:
    if _wake_event is not None:
        _wake_event.set()


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


async def enqueue_mail(
    to: str | list[str],
    subject: str,
    body_html: str,
) -> None:
    """Insert into mail_queue. The worker claims rows atomically from the DB."""
    recipients = filter_recipients([to] if isinstance(to, str) else list(to))
    if not recipients:
        return

    admin = get_supabase_admin()
    inserted = False
    for recipient in recipients:
        if _recent_duplicate(recipient, subject):
            logger.info(
                "Skipping duplicate mail enqueue (recipient=%s subject=%r)",
                recipient,
                subject,
            )
            continue
        try:
            admin.table("mail_queue").insert({
                "recipient": recipient,
                "subject": subject,
                "body_html": body_html,
                "status": "pending",
            }).execute()
            inserted = True
        except Exception as exc:
            logger.warning("Failed to enqueue mail to %s: %s", recipient, exc)

    if inserted:
        _signal_worker()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _claim_next() -> dict[str, Any] | None:
    admin = get_supabase_admin()
    try:
        r = admin.rpc("claim_next_mail_queue_item", {}).execute()
        _note_db_success()
        if r.data:
            return r.data[0] if isinstance(r.data, list) else r.data
        return None
    except Exception as exc:
        if _is_transient_network_error(exc):
            _note_db_failure(exc)
            return None
        logger.warning("claim_next_mail_queue_item RPC failed: %s", exc)
        return _claim_next_fallback()


def _claim_next_fallback() -> dict[str, Any] | None:
    """Best-effort claim when the RPC migration has not been applied yet."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("mail_queue")
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(1)
            .execute()
        )
        if not r.data:
            _note_db_success()
            return None
        row = r.data[0]
        upd = (
            admin.table("mail_queue")
            .update({"status": "processing"})
            .eq("id", row["id"])
            .eq("status", "pending")
            .execute()
        )
        if upd.data:
            _note_db_success()
            return row
    except Exception as exc:
        if _is_transient_network_error(exc):
            _note_db_failure(exc)
        else:
            logger.warning("mail claim fallback failed: %s", exc)
    return None


async def _send_and_mark(record: dict[str, Any]) -> None:
    from mail.send import _send_html  # lazy to avoid circular import

    mail_id = record["id"]
    try:
        await _send_html(
            to=record["recipient"],
            subject=record["subject"],
            body_html=record["body_html"],
        )
        admin = get_supabase_admin()
        admin.table("mail_queue").update({
            "status": "sent",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", mail_id).execute()
    except Exception as exc:
        logger.warning("Mail send failed (id=%s): %s", mail_id, exc)
        admin = get_supabase_admin()
        retries = (record.get("retries") or 0) + 1
        new_status = "failed" if retries >= 3 else "pending"
        admin.table("mail_queue").update({
            "status": new_status,
            "error": str(exc)[:500],
            "retries": retries,
        }).eq("id", mail_id).execute()


def _reset_processing_on_startup() -> None:
    """Re-queue rows left in 'processing' after a crash or deploy."""
    try:
        admin = get_supabase_admin()
        admin.table("mail_queue").update({"status": "pending"}).eq("status", "processing").execute()
    except Exception as exc:
        logger.warning("Failed to reset processing mail rows: %s", exc)


async def worker_loop() -> None:
    global _wake_event
    _wake_event = asyncio.Event()
    _reset_processing_on_startup()
    logger.info("Mail worker: started (poll interval=%ss)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            record = _claim_next()
            if record:
                await _send_and_mark(record)
                continue

            _wake_event.clear()
            try:
                await asyncio.wait_for(
                    _wake_event.wait(),
                    timeout=_current_poll_interval(),
                )
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            logger.info("Mail worker: cancelled, shutting down")
            break
        except Exception as exc:
            if _is_transient_network_error(exc):
                await asyncio.sleep(_note_db_failure(exc))
            else:
                logger.warning("Mail worker: unexpected error: %s", exc)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def start_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(worker_loop())
        logger.info("Mail worker task created")


async def stop_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
        logger.info("Mail worker stopped")
