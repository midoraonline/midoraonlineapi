"""Presence heartbeats and merchant shop availability sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

ACTIVE_INSTANCE_GRACE_MINUTES = 2


def touch_user_last_seen(admin: Any, user_id: str, now_iso: str) -> None:
    try:
        admin.table("users").update({"last_seen_at": now_iso}).eq("id", user_id).execute()
    except Exception as exc:
        logger.warning("touch_user_last_seen(%s) failed: %s", user_id, exc)


def set_merchant_shops_available(admin: Any, user_id: str, now_iso: str) -> None:
    """Merchant is actively online — mark owned shops as available."""
    try:
        admin.table("shops").update({
            "available_now": True,
            "last_seen_at": now_iso,
        }).eq("owner_id", user_id).execute()
    except Exception as exc:
        logger.warning("set_merchant_shops_available(%s) failed: %s", user_id, exc)


def clear_merchant_shops_if_idle(admin: Any, user_id: str) -> None:
    """Mark shops unavailable only when no other tab/instance is still active."""
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=ACTIVE_INSTANCE_GRACE_MINUTES)
    ).isoformat()
    try:
        remaining = (
            admin.table("online_presence")
            .select("instance_id")
            .eq("user_id", user_id)
            .gte("last_seen_at", since)
            .limit(1)
            .execute()
        )
        if remaining.data:
            return
        admin.table("shops").update({"available_now": False}).eq("owner_id", user_id).execute()
    except Exception as exc:
        logger.warning("clear_merchant_shops_if_idle(%s) failed: %s", user_id, exc)


def record_presence(
    admin: Any,
    instance_id: str,
    user_id: str | None,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "instance_id": instance_id,
        "last_seen_at": now_iso,
    }
    if user_id:
        payload["user_id"] = user_id

    admin.table("online_presence").upsert(payload, on_conflict="instance_id").execute()

    if user_id:
        touch_user_last_seen(admin, user_id, now_iso)
        set_merchant_shops_available(admin, user_id, now_iso)


def remove_presence(admin: Any, instance_id: str) -> str | None:
    """Delete one instance row; return the user_id that was linked (if any)."""
    user_id: str | None = None
    try:
        row = (
            admin.table("online_presence")
            .select("user_id")
            .eq("instance_id", instance_id)
            .limit(1)
            .execute()
        )
        if row.data:
            raw = row.data[0].get("user_id")
            if raw:
                user_id = str(raw)
        admin.table("online_presence").delete().eq("instance_id", instance_id).execute()
    except Exception as exc:
        logger.warning("remove_presence(%s) failed: %s", instance_id[:8], exc)
    return user_id
