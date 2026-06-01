from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from notifications.service import broadcast_notification

router = APIRouter()


@router.post("/notifications/broadcast")
async def admin_broadcast_notification(
    title: str = Query(...),
    body: str | None = Query(None),
    user_ids: str | None = Query(None, description="Comma-separated user IDs. Omit to send to all users."),
) -> dict[str, Any]:
    """Admin: broadcast a notification to all users or a subset."""
    target_ids = None
    if user_ids:
        target_ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]

    created = broadcast_notification(
        title=title,
        body=body,
        user_ids=target_ids,
    )

    return {
        "status": "ok",
        "notifications_created": created,
        "audience": "all_users" if not target_ids else f"{len(target_ids)} users",
    }
