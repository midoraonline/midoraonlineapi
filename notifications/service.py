from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

_CHANNELS = {"in-app", "sms", "whatsapp", "email", "push"}


def create_notification(
    user_id: str,
    title: str,
    body: str | None = None,
    channel: str = "in-app",
    metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Create a notification for a user."""
    if channel not in _CHANNELS:
        channel = "in-app"

    admin = get_supabase_admin()
    payload = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "channel": channel,
        "status": "unread",
        "metadata": metadata or {},
    }
    try:
        r = admin.table("notifications").insert(payload).execute()
        return r.data[0] if r.data else None
    except Exception as exc:
        logger.warning("create_notification failed: %s", exc)
        return None


def list_notifications(
    user_id: str,
    page: int = 1,
    limit: int = 20,
    unread_only: bool = False,
) -> dict:
    """Paginated list of notifications for a user."""
    admin = get_supabase_admin()
    limit = min(limit, 100)
    offset = (page - 1) * limit

    q = admin.table("notifications").select("*", count="exact").eq("user_id", user_id)
    if unread_only:
        q = q.eq("status", "unread")

    r = q.range(offset, offset + limit - 1).order("created_at", desc=True).execute()
    total = r.count if hasattr(r, "count") and r.count is not None else len(r.data or [])
    total_pages = (total + limit - 1) // limit if limit else 0

    return {
        "items": r.data or [],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_unread_count(user_id: str) -> int:
    """Get count of unread notifications for a user."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("notifications")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("status", "unread")
            .limit(1)
            .execute()
        )
        return r.count or 0
    except Exception as exc:
        logger.warning("get_unread_count(%s) failed: %s", user_id, exc)
        return 0


def mark_as_read(notification_id: str) -> dict | None:
    """Mark a single notification as read."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("notifications")
            .update({"status": "read"})
            .eq("id", notification_id)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as exc:
        logger.warning("mark_as_read(%s) failed: %s", notification_id, exc)
        return None


def mark_all_as_read(user_id: str) -> int:
    """Mark all unread notifications as read for a user. Returns count affected."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("notifications")
            .update({"status": "read"})
            .eq("user_id", user_id)
            .eq("status", "unread")
            .execute()
        )
        return len(r.data or [])
    except Exception as exc:
        logger.warning("mark_all_as_read(%s) failed: %s", user_id, exc)
        return 0


def broadcast_notification(
    title: str,
    body: str | None = None,
    user_ids: list[str] | None = None,
    admin_key: str | None = None,
) -> int:
    """Broadcast a notification to specific users or all users.
    Requires admin authorization (checked by caller).
    """
    admin = get_supabase_admin()
    created = 0
    try:
        if user_ids:
            targets = user_ids
        else:
            ur = admin.table("users").select("id").execute()
            targets = [u["id"] for u in (ur.data or [])]

        for uid in targets:
            payload = {
                "user_id": uid,
                "title": title,
                "body": body,
                "channel": "in-app",
                "status": "unread",
            }
            admin.table("notifications").insert(payload).execute()
            created += 1
        return created
    except Exception as exc:
        logger.warning("broadcast_notification failed after %d inserts: %s", created, exc)
        return created
