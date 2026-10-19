"""Admin and cron entry points for the dead-image sweep."""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from core.security import get_current_user_id

from core.config import get_settings
from db.supabase import get_supabase_admin
from media.registry import (
    KINDS,
    discard_uploads,
    register_upload,
    requested_keys,
    sweep_orphan_uploads,
)
from media.sweep import run_dead_image_sweep

admin_router = APIRouter()
cron_router = APIRouter()
user_router = APIRouter()


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
    client = get_supabase_admin()
    result = await run_dead_image_sweep(client)
    try:
        result["uploads"] = await sweep_orphan_uploads(client)
    except Exception as exc:  # never fail the dead-image sweep over orphans
        result["uploads"] = {"error": str(exc)}
    return result


def authorize_register(presented: Optional[str]) -> None:
    """Server-to-server secret shared with the website (MEDIA_REGISTER_SECRET)."""
    settings = get_settings()
    secret = (os.getenv("MEDIA_REGISTER_SECRET") or "").strip()
    env = os.getenv("VERCEL_ENV") or settings.environment
    if not secret:
        if settings.is_production or env == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MEDIA_REGISTER_SECRET is not configured on this deployment",
            )
        return
    token = (presented or "").strip()
    if not token or not secrets.compare_digest(token, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")


class RegisterUploadBody(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    user_id: str = Field(min_length=1, max_length=64)
    kind: str


class DiscardBody(BaseModel):
    keys: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


@cron_router.post("/uploads")
def register_media_upload(
    body: RegisterUploadBody,
    x_media_secret: Optional[str] = Header(default=None),
) -> dict:
    """Website (UploadThing onUploadComplete): record who uploaded a file."""
    authorize_register(x_media_secret)
    if body.kind not in KINDS:
        raise HTTPException(status_code=400, detail="unknown kind")
    stored = register_upload(
        get_supabase_admin(), key=body.key, url=body.url, user_id=body.user_id, kind=body.kind
    )
    return {"stored": stored}


@user_router.post("/discard")
async def discard_media(body: DiscardBody, user_id: str = Depends(get_current_user_id)) -> dict:
    """Delete files the caller uploaded that no listing, shop or profile uses."""
    keys = requested_keys(body.keys, body.urls)
    return await discard_uploads(get_supabase_admin(), user_id, keys)


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
