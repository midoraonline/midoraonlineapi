"""Admin audit history: who changed a plan, a user role or a listing status."""
from __future__ import annotations

import logging
from typing import Any, Literal

from core.security import TokenPayload

logger = logging.getLogger(__name__)

TargetType = Literal["plan", "user", "listing"]
TARGET_TYPES: tuple[str, ...] = ("plan", "user", "listing")


def actor_id(payload: TokenPayload | None) -> str | None:
    return str(payload.sub) if payload and payload.sub else None


def record_admin_action(
    client,
    *,
    actor: str | None,
    action: str,
    target_type: TargetType,
    target_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Best-effort insert; the admin change itself must not fail if audit is down."""
    row = {
        "actor_id": actor,
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id),
        "before": before,
        "after": after,
    }
    try:
        client.table("admin_audit_log").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin audit insert failed (run migration 061?): %s", exc)


def list_admin_actions(
    client,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    offset = (page - 1) * limit
    query = client.table("admin_audit_log").select("*", count="exact")
    if target_type in TARGET_TYPES:
        query = query.eq("target_type", target_type)
    if target_id:
        query = query.eq("target_id", target_id)
    result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    items = result.data or []
    total = result.count if getattr(result, "count", None) is not None else len(items)
    return {"items": _with_actor_names(client, items), "total": total, "page": page, "limit": limit}


def _with_actor_names(client, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = sorted({str(i["actor_id"]) for i in items if i.get("actor_id")})
    if not ids:
        return items
    rows = client.table("users").select("id, full_name, email").in_("id", ids).execute().data or []
    names = {str(r["id"]): r.get("full_name") or r.get("email") for r in rows}
    return [{**i, "actor_name": names.get(str(i.get("actor_id")))} for i in items]
