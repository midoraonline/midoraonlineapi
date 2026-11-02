"""Server-side analytics events for actions the browser can't report reliably."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def record_event(
    event_type: str,
    *,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    properties: dict[str, Any] | None = None,
    client: Any | None = None,
) -> None:
    try:
        if client is None:
            from db.supabase import get_supabase_admin

            client = get_supabase_admin()
        client.table("analytics_events").insert({
            "event_type": event_type,
            "actor_id": actor_id,
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
            "properties": properties or {},
            "source": "server",
            "ts": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("server analytics event %s failed: %s", event_type, exc)
