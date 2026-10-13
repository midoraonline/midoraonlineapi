"""Cron entrypoint that sends queued mail on serverless deployments.

Vercel never runs the in-process mail worker, so anything not sent inline
by `enqueue_mail` is picked up here. Auth matches the moderation drain:
`Authorization: Bearer <CRON_SECRET>`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header

from listingModeration.routes.drain import _authorize
from mail.queue import drain_mail_queue, reset_stuck_processing

router = APIRouter()


async def _run() -> dict[str, int]:
    reset_stuck_processing()
    return await drain_mail_queue(50)


@router.get("/drain")
async def mail_drain_get(authorization: Optional[str] = Header(default=None)) -> dict[str, int]:
    _authorize(authorization)
    return await _run()


@router.post("/drain")
async def mail_drain_post(authorization: Optional[str] = Header(default=None)) -> dict[str, int]:
    _authorize(authorization)
    return await _run()
