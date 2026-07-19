from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from postgrest.exceptions import APIError as PostgrestAPIError

from core.security import get_current_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateConversationBody(BaseModel):
    seller_id: str
    shop_id: str | None = None
    product_id: str | None = None


class SendMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_participant_ids(conversation_id: str) -> tuple[str, str] | None:
    try:
        admin = get_supabase_admin()
        r = (
            admin.table("conversations")
            .select("buyer_id, seller_id")
            .eq("id", conversation_id)
            .execute()
        )
        if not r.data:
            return None
        return (r.data[0]["buyer_id"], r.data[0]["seller_id"])
    except PostgrestAPIError:
        return None
    except Exception as exc:
        logger.warning("_get_participant_ids error: %s", exc)
        return None


def _unread_for(user_id: str) -> int:
    try:
        admin = get_supabase_admin()
        b = (
            admin.table("conversations")
            .select("id")
            .eq("buyer_id", user_id)
            .gte("buyer_unread", 1)
            .execute()
        )
        s = (
            admin.table("conversations")
            .select("id")
            .eq("seller_id", user_id)
            .gte("seller_unread", 1)
            .execute()
        )
        return len(b.data or []) + len(s.data or [])
    except PostgrestAPIError:
        return 0
    except Exception as exc:
        logger.warning("_unread_for error: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/chat/conversations")
async def list_conversations(
    limit: int = Query(50, ge=1, le=100),
    current_user_id: str = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("conversations")
            .select("*, buyer:buyer_id(full_name), seller:seller_id(full_name)")
            .or_(f"buyer_id.eq.{current_user_id},seller_id.eq.{current_user_id}")
            .order("last_message_at", desc=True, nullsfirst=False)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("list_conversations failed: %s", exc)
        return []


@router.post("/chat/conversations")
async def create_conversation(
    body: CreateConversationBody,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if current_user_id == body.seller_id:
        raise HTTPException(status_code=400, detail="Cannot start conversation with yourself")

    admin = get_supabase_admin()
    try:
        existing = (
            admin.table("conversations")
            .select("*, buyer:buyer_id(full_name), seller:seller_id(full_name)")
            .or_(
                f"and(buyer_id.eq.{current_user_id},seller_id.eq.{body.seller_id}),"
                f"and(buyer_id.eq.{body.seller_id},seller_id.eq.{current_user_id})"
            )
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            return existing.data[0]

        r = admin.table("conversations").insert({
            "buyer_id": current_user_id,
            "seller_id": body.seller_id,
            "shop_id": body.shop_id,
            "product_id": body.product_id,
        }).execute()
        if not r.data:
            raise HTTPException(status_code=502, detail="Failed to create conversation")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("create_conversation failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to create conversation")


@router.get("/chat/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    before: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    ids = _get_participant_ids(conversation_id)
    if not ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if current_user_id not in ids:
        raise HTTPException(status_code=403, detail="Not a participant")

    admin = get_supabase_admin()
    try:
        query = (
            admin.table("messages")
            .select("*, sender:sender_id(full_name)")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(50)
        )
        if before:
            query = query.lt("id", before)
        r = query.execute()
        msgs = r.data or []
        msgs.reverse()
        return msgs
    except Exception as exc:
        logger.warning("list_messages failed: %s", exc)
        return []


@router.post("/chat/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: SendMessageBody,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    ids = _get_participant_ids(conversation_id)
    if not ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if current_user_id not in ids:
        raise HTTPException(status_code=403, detail="Not a participant")

    content = body.content.strip()
    is_buyer = current_user_id == ids[0]
    recipient_id = ids[1] if is_buyer else ids[0]
    admin = get_supabase_admin()

    try:
        msg = admin.table("messages").insert({
            "conversation_id": conversation_id,
            "sender_id": current_user_id,
            "content": content,
        }).execute()

        now_iso = datetime.now(timezone.utc).isoformat()
        unread_field = "seller_unread" if is_buyer else "buyer_unread"
        admin.rpc("increment_unread", {
            "p_field": unread_field,
            "p_conversation_id": conversation_id,
        }).execute()

        admin.table("conversations").update({
            "last_message": content[:100],
            "last_message_at": now_iso,
            "updated_at": now_iso,
        }).eq("id", conversation_id).execute()

        if not msg.data:
            raise HTTPException(status_code=502, detail="Failed to send message")

        # Fire-and-forget browser push. Never blocks the response — if the
        # push service is down or the user has no subscriptions, we still
        # return 200 (Supabase Realtime already delivered the row to open
        # tabs).
        try:
            _notify_recipient(recipient_id, current_user_id, conversation_id, content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("push notify failed: %s", exc)

        return msg.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("send_message failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to send message")


def _notify_recipient(
    recipient_id: str,
    sender_id: str,
    conversation_id: str,
    content: str,
) -> None:
    """Send a Web Push notification to the message recipient."""
    from notifications.push_service import send_to_user  # local import: pywebpush optional

    admin = get_supabase_admin()

    # Look up the sender's display name for the notification title. Best-
    # effort: if the query fails we still send with a generic title.
    sender_name = "New message"
    try:
        r = (
            admin.table("users")
            .select("full_name")
            .eq("id", sender_id)
            .limit(1)
            .execute()
        )
        if r.data and r.data[0].get("full_name"):
            sender_name = str(r.data[0]["full_name"])
    except Exception:
        pass

    preview = content[:140]
    send_to_user(
        recipient_id,
        {
            "title": sender_name,
            "body": preview,
            "url": f"/chat?conversation={conversation_id}",
            "tag": f"chat:{conversation_id}",
        },
    )


@router.put("/chat/conversations/{conversation_id}/read")
async def mark_read(
    conversation_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    ids = _get_participant_ids(conversation_id)
    if not ids:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if current_user_id not in ids:
        raise HTTPException(status_code=403, detail="Not a participant")

    is_buyer = current_user_id == ids[0]
    unread_field = "buyer_unread" if is_buyer else "seller_unread"
    admin = get_supabase_admin()

    try:
        (
            admin.table("messages")
            .update({"read_at": datetime.now(timezone.utc).isoformat()})
            .eq("conversation_id", conversation_id)
            .eq("read_at", None)
            .neq("sender_id", current_user_id)
            .execute()
        )
        admin.table("conversations").update({unread_field: 0}).eq("id", conversation_id).execute()
        return {"status": "read"}
    except Exception as exc:
        logger.warning("mark_read failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to mark as read")


@router.get("/chat/unread")
async def unread_count(
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return {"unread_count": _unread_for(current_user_id)}


# ---------------------------------------------------------------------------
# NOTE: The former `/ws/chat/{conversation_id}` WebSocket endpoint was removed
# in favour of Supabase Realtime. Vercel's serverless Python runtime kills
# long-lived connections at the function's max duration (10s hobby / 60s pro),
# and per-instance in-memory client tracking cannot fan out across cold
# starts. Clients now subscribe directly to `public.messages` /
# `public.conversations` via `useRealtimeTable`; RLS on those tables
# (see migration 024) restricts events to conversation participants.
# ---------------------------------------------------------------------------
