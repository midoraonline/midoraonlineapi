"""Async email queue backed by Postgres + in-memory asyncio worker.

All email sends go through `enqueue_mail()` which inserts into `mail_queue`
and pushes to an in-memory `asyncio.Queue`. A background worker picks up
pending emails, sends them, and marks them as sent/failed in the DB.

On server start, the worker first processes any pending emails left over from
previous runs before picking up new ones.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
_worker_task: asyncio.Task[Any] | None = None
POLL_INTERVAL_SECONDS = 2


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


async def enqueue_mail(
    to: str | list[str],
    subject: str,
    body_html: str,
) -> None:
    """Insert into mail_queue and push onto the in-memory queue."""
    recipients = [to] if isinstance(to, str) else [r for r in to if r]
    for recipient in recipients:
        admin = get_supabase_admin()
        try:
            r = (
                admin.table("mail_queue")
                .insert({
                    "recipient": recipient,
                    "subject": subject,
                    "body_html": body_html,
                    "status": "pending",
                })
                .execute()
            )
            if r.data:
                await _queue.put(r.data[0])
        except Exception as exc:
            logger.warning("Failed to enqueue mail to %s: %s", recipient, exc)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _send_and_mark(record: dict[str, Any]) -> None:
    """Send one email and update its DB status."""
    from mail.send import _send_html  # lazy to avoid circular import

    mail_id = record["id"]
    try:
        await _send_html(to=record["recipient"], subject=record["subject"], body_html=record["body_html"])
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


async def _process_pending_from_db() -> None:
    """On startup, re-enqueue any pending emails left from before a restart."""
    try:
        admin = get_supabase_admin()
        r = (
            admin.table("mail_queue")
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .execute()
        )
        for row in r.data or []:
            await _queue.put(row)
    except Exception as exc:
        logger.warning("Failed to load pending mail from DB: %s", exc)


async def worker_loop() -> None:
    """Continuously send emails from the in-memory queue."""
    logger.info("Mail worker: processing pending emails from DB...")
    await _process_pending_from_db()
    logger.info("Mail worker: started (poll interval=%ss)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            record = await _queue.get()
            await _send_and_mark(record)
        except asyncio.CancelledError:
            logger.info("Mail worker: cancelled, shutting down")
            break
        except Exception as exc:
            logger.warning("Mail worker: unexpected error: %s", exc)


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
