"""GET/POST /moderation/drain — cron-triggered pipeline runner.

Vercel Cron Jobs make a GET request to a URL on schedule. Supabase pg_cron
uses POST via `net.http_post`. We accept both.

Auth: `Authorization: Bearer <CRON_SECRET>` — Vercel automatically injects
this header when you set the `CRON_SECRET` project env var. When the header
is missing / wrong, we 401. If no `CRON_SECRET` is configured at all we
refuse to run in production (safer than silently exposing the drain to the
open internet).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status

from .. import pipeline
from ..config import config
from ..schemas import DrainResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _authorize(authorization: Optional[str]) -> None:
    secret = os.getenv("CRON_SECRET", "").strip()
    env = os.getenv("VERCEL_ENV") or os.getenv("ENVIRONMENT", "development")

    if not secret:
        # Production without a secret is a footgun — refuse.
        if env == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CRON_SECRET is not configured on this deployment",
            )
        return  # dev / local: allow unauthenticated drain for convenience

    expected = f"Bearer {secret}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid cron authorization",
        )


async def _run_drain() -> DrainResponse:
    counts = await pipeline.process_batch(config.batch_size)
    return DrainResponse(
        reclaimed=counts.get("reclaimed", 0),
        processed=counts.get("processed", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        needs_review=counts.get("needs_review", 0),
        failed=counts.get("failed", 0),
    )


@router.get("/drain", response_model=DrainResponse)
async def drain_get(authorization: Optional[str] = Header(default=None)) -> DrainResponse:
    """GET entrypoint used by Vercel Cron (which only issues GETs)."""
    _authorize(authorization)
    return await _run_drain()


@router.post("/drain", response_model=DrainResponse)
async def drain_post(authorization: Optional[str] = Header(default=None)) -> DrainResponse:
    """POST entrypoint used by Supabase pg_cron / manual replay."""
    _authorize(authorization)
    return await _run_drain()
