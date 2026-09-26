"""Admin and cron entry points for the dead-image sweep."""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status

from core.config import get_settings
from db.supabase import get_supabase_admin
from media.sweep import run_dead_image_sweep

admin_router = APIRouter()
cron_router = APIRouter()


def authorize_cron(authorization: Optional[str]) -> None:
    settings = get_settings()
    secret = (settings.cron_secret or os.getenv("CRON_SECRET", "")).strip()
    env = os.getenv("VERCEL_ENV") or settings.environment
    if not secret:
        if settings.is_production or env == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CRON_SECRET is not configured on this deployment",
            )
        return
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not presented or not secrets.compare_digest(presented, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid cron authorization",
        )


async def _sweep() -> dict:
    return await run_dead_image_sweep(get_supabase_admin())


@admin_router.post("/media/sweep")
async def admin_sweep_dead_images() -> dict:
    """Admin: HEAD-check active listing images and drop 404/410 URLs."""
    return await _sweep()


@cron_router.get("/sweep")
async def cron_sweep_get(authorization: Optional[str] = Header(default=None)) -> dict:
    authorize_cron(authorization)
    return await _sweep()


@cron_router.post("/sweep")
async def cron_sweep_post(authorization: Optional[str] = Header(default=None)) -> dict:
    authorize_cron(authorization)
    return await _sweep()
