"""In-process moderation drain worker.

Runs `pipeline.process_batch` on a short poll interval so listings get
auto-moderated in development and other long-lived deployments without
manual `curl`ing the drain endpoint.

Not started on Vercel — production relies on `vercel.json`'s cron entry
hitting `GET /api/v1/moderation/drain` on schedule.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import pipeline
from .config import config

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_DEFAULT_POLL_SECONDS = 15.0


def _poll_interval() -> float:
    raw = os.getenv("MODERATION_WORKER_POLL_SECONDS")
    if not raw:
        return _DEFAULT_POLL_SECONDS
    try:
        v = float(raw)
        return max(2.0, v)
    except ValueError:
        return _DEFAULT_POLL_SECONDS


async def _loop() -> None:
    interval = _poll_interval()
    logger.info("Moderation worker: started (poll interval=%ss)", interval)
    while True:
        try:
            counts = await pipeline.process_batch(config.batch_size)
            if counts.get("processed"):
                logger.info(
                    "Moderation worker: %s processed (approved=%s rejected=%s "
                    "needs_review=%s failed=%s)",
                    counts.get("processed"),
                    counts.get("approved"),
                    counts.get("rejected"),
                    counts.get("needs_review"),
                    counts.get("failed"),
                )
                # Keep draining while the queue has work.
                continue
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Moderation worker: cancelled, shutting down")
            break
        except Exception as exc:
            logger.warning("Moderation worker: unexpected error: %s", exc)
            await asyncio.sleep(interval)


def start_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_loop())
        logger.info("Moderation worker task created")


async def stop_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
        logger.info("Moderation worker stopped")
