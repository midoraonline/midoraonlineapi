from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from core.security import get_current_user_id
from notifications.service import (
    list_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
)

router = APIRouter()


@router.get("")
async def get_user_notifications(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """List notifications for the current user."""
    return list_notifications(
        user_id=current_user_id,
        page=page,
        limit=limit,
        unread_only=unread_only,
    )


@router.get("/count")
async def get_notification_count(
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Get unread notification count."""
    count = get_unread_count(current_user_id)
    return {"unread_count": count}


@router.patch("/{notification_id}/read")
async def read_notification(
    notification_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Mark a notification as read."""
    result = mark_as_read(notification_id)
    if not result:
        return {"error": "Notification not found"}
    return result


@router.post("/read-all")
async def read_all_notifications(
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Mark all notifications as read for the current user."""
    count = mark_all_as_read(current_user_id)
    return {"status": "ok", "marked_read": count}
