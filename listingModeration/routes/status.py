"""GET /moderation/listings/{id} and GET /moderation/listings — status polling."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from core.config import get_settings
from db.supabase import get_supabase_admin

from .. import service
from ..config import config
from ..schemas import ModerationRow, ModerationStatus

router = APIRouter()


@router.get("/listings/{listing_id}", response_model=ModerationRow)
def get_status(listing_id: UUID) -> ModerationRow:
    row = service.get_by_id(listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="moderation row not found")
    return row


@router.get("/listings", response_model=list[ModerationRow])
def list_by_status(
    status: Optional[ModerationStatus] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ModerationRow]:
    """List rows by status. Primarily for a reviewer UI on `needs_review`."""
    return service.list_by_status(status or ModerationStatus.NEEDS_REVIEW, limit=limit)


@router.get("/health")
def health() -> dict:
    """Operational snapshot: queue depth per status + config sanity checks.

    Used to diagnose "listings stuck in Reviewing" without SQL access. Also
    reports whether the pipeline can actually decide (Gemini configured,
    inline mode on) and whether cron will fire (needs CRON_SECRET in prod).
    """
    settings = get_settings()
    admin = get_supabase_admin()

    counts: dict[str, int] = {}
    for st in ("pending", "processing", "approved", "rejected", "needs_review", "failed"):
        try:
            r = (
                admin.table("listing_moderation_queue")
                .select("id", count="exact")
                .eq("status", st)
                .limit(1)
                .execute()
            )
            counts[st] = int(getattr(r, "count", 0) or 0)
        except Exception:
            counts[st] = -1

    import os

    return {
        "queue": counts,
        "config": {
            "inline_on_enqueue": config.inline_on_enqueue,
            "inline_timeout_seconds": config.inline_timeout_seconds,
            "fail_open_when_model_unavailable": config.fail_open_when_model_unavailable,
            "batch_size": config.batch_size,
            "stuck_after_seconds": config.stuck_after_seconds,
            "enable_profanity_check": config.enable_profanity_check,
            "enable_openai_free_api": config.enable_openai_free_api,
        },
        "gemini_configured": bool(settings.gemini_api_key),
        "openai_configured": bool(getattr(settings, "openai_api_key", "")),
        "cron_secret_configured": bool(os.getenv("CRON_SECRET", "").strip()),
        "environment": os.getenv("VERCEL_ENV") or os.getenv("ENVIRONMENT", "development"),
    }
