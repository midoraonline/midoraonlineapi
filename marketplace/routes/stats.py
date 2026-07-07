from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.security import get_optional_user_id
from db.supabase import get_supabase_admin
from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter()

WINDOW_MINUTES = 15
INSTANCE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class PresencePingRequest(BaseModel):
    instance_id: str = Field(..., min_length=8, max_length=64)


def _valid_instance_id(value: str) -> bool:
    v = (value or "").strip()
    if not v or len(v) > 64:
        return False
    if INSTANCE_ID_RE.match(v):
        return True
    return v.startswith("midora-")


def _cleanup_stale_presence(admin: Any, older_than_hours: int = 2) -> None:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        admin.table("online_presence").delete().lt("last_seen_at", cutoff).execute()
    except Exception as exc:
        logger.warning("online_presence cleanup failed: %s", exc)


@router.post("/presence/ping")
async def ping_presence(
    body: PresencePingRequest,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, str]:
    """Heartbeat from a single app instance (browser tab). Upserts by instance_id."""
    instance_id = body.instance_id.strip()
    if not _valid_instance_id(instance_id):
        raise HTTPException(status_code=400, detail="Invalid instance_id")

    admin = get_supabase_admin()
    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "instance_id": instance_id,
        "last_seen_at": now_iso,
    }
    if user_id:
        payload["user_id"] = user_id

    try:
        admin.table("online_presence").upsert(payload, on_conflict="instance_id").execute()
    except Exception as exc:
        logger.warning("presence ping failed (instance=%s): %s", instance_id[:8], exc)
        raise HTTPException(status_code=500, detail="Failed to record presence") from exc

    _cleanup_stale_presence(admin)
    return {"status": "ok"}


@router.get("/online-users")
async def get_online_users() -> dict[str, Any]:
    """Count distinct app instances active in the last 15 minutes."""
    admin = get_supabase_admin()
    since = (datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)).isoformat()

    count = 0
    try:
        r = (
            admin.table("online_presence")
            .select("instance_id", count="exact")
            .gte("last_seen_at", since)
            .execute()
        )
        count = int(r.count or 0)
    except Exception as exc:
        logger.warning("online-users presence query failed: %s", exc)

    if count == 0:
        count = _fallback_activity_count(admin, since)

    if count == 0:
        count = 1

    return {
        "online_count": count,
        "window_minutes": WINDOW_MINUTES,
    }


def _fallback_activity_count(admin: Any, since: str) -> int:
    """Legacy fallback when presence table is empty or migration not applied yet."""
    active_user_ids: set[str] = set()

    for table, col in (
        ("listing_events", "buyer_id"),
        ("search_history", "user_id"),
        ("product_likes", "user_id"),
    ):
        try:
            r = (
                admin.table(table)
                .select(col)
                .gte("created_at", since)
                .not_.is_(col, "null")
                .limit(2000)
                .execute()
            )
            for row in r.data or []:
                uid = row.get(col)
                if uid:
                    active_user_ids.add(str(uid))
        except Exception as exc:
            logger.warning("online-users fallback %s failed: %s", table, exc)

    return len(active_user_ids)
