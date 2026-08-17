"""Application lifespan.

Kept intentionally minimal for serverless (Vercel) deployments where the
lifespan runs on *every* cold start. Feature modules own their workers
(`MailModule`, `ListingModerationModule`); this file only iterates them.

On Vercel, in-process workers are skipped — queue rows still land in
Postgres and a cron drain processes them.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from app.factory.routers import start_module_workers
from core.runtime import is_serverless

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    modules = getattr(app.state, "modules", [])
    started: list = []
    if is_serverless():
        logger.info(
            "Serverless environment detected — skipping in-process workers. "
            "Drain queues via cron instead."
        )
    else:
        started = start_module_workers(modules)

    yield

    for module in reversed(started):
        await module.on_shutdown()
