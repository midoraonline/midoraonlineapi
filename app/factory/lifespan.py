"""Application lifespan.

Kept intentionally minimal for serverless (Vercel) deployments where the
lifespan runs on *every* cold start. Anything expensive here directly hurts
cold-start p95.

The mail queue worker (`start_worker`) launches an asyncio background task
that survives only while a container is warm. On Vercel it will not reliably
drain the queue — `enqueue_mail()` still writes to Postgres, but you must
add a Supabase cron job (or Vercel Cron) that hits a drain endpoint to
process pending mail rows in production. For local `uvicorn --reload` and
non-serverless deployments the worker still runs and drains normally.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from mail.queue import start_worker, stop_worker

logger = logging.getLogger(__name__)


def _is_serverless() -> bool:
    # Vercel sets these at runtime. Guard with either.
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))


@asynccontextmanager
async def lifespan(app):
    started = False
    if not _is_serverless():
        start_worker()
        started = True
    else:
        logger.info(
            "Serverless environment detected — skipping in-process mail worker. "
            "Drain `mail_queue` via a cron job instead."
        )

    yield

    if started:
        await stop_worker()

