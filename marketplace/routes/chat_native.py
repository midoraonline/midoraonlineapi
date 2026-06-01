from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from postgrest.exceptions import APIError as PostgrestAPIError

from core.security import _decode_auth_token, get_current_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _table_exists() -> bool:
    """Check if chat tables exist in the database schema."""
    try:
        admin = get_supabase_admin()
        admin.table("conversations").select("id").limit(1).execute()
        return True
    except PostgrestAPIError:
        return False
    except Exception:
        return False


def _get_participant_ids(conversation_id: str) -> tuple[str, str] | None:
    try:
        admin = get_supabase_admin()
        r = admin.table("conversations").select("buyer_id, seller_id").eq("id", conversation_id).execute()
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
        b = admin.table("conversations").select("id").eq("buyer_id", user_id).gte("buyer_unread", 1).execute()
        s = admin.table("conversations").select("id").eq("seller_id", user_id).gte("seller_unread", 1).execute()
        return len(b.data or []) + len(s.data or [])
    except PostgrestAPIError:
        return 0
    except Exception as exc:
        logger.warning("_unread_for error: %s", exc)
        return 0


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


from pydantic import BaseModel


class CreateConversationBody(BaseModel):
    seller_id: str
    shop_id: str | None = None
    product_id: str | None = None


@router.post("/chat/conversations")
async def create_conversation(
    body: CreateConversationBody,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if current_user_id == body.seller_id:
        return {"error": "Cannot start conversation with yourself"}
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
        return r.data[0] if r.data else {"status": "created"}
    except Exception as exc:
        logger.warning("create_conversation failed: %s", exc)
        return {"error": "Failed to create conversation"}


@router.get("/chat/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    before: str | None = Query(None),
    current_user_id: str = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    ids = _get_participant_ids(conversation_id)
    if not ids or current_user_id not in ids:
        return []
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
    content: str = Query(..., min_length=1, max_length=2000),
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    ids = _get_participant_ids(conversation_id)
    if not ids:
        return {"error": "Conversation not found"}
    if current_user_id not in ids:
        return {"error": "Not a participant"}

    is_buyer = current_user_id == ids[0]
    admin = get_supabase_admin()
    try:
        msg = admin.table("messages").insert({
            "conversation_id": conversation_id,
            "sender_id": current_user_id,
            "content": content.strip(),
        }).execute()

        now_iso = datetime.now(timezone.utc).isoformat()
        unread_field = "seller_unread" if is_buyer else "buyer_unread"
        admin.rpc("increment_unread", {
            "p_field": unread_field,
            "p_conversation_id": conversation_id,
        }).execute()

        admin.table("conversations").update({
            "last_message": content.strip()[:100],
            "last_message_at": now_iso,
            "updated_at": now_iso,
        }).eq("id", conversation_id).execute()

        return msg.data[0] if msg.data else {"status": "sent"}
    except Exception as exc:
        logger.warning("send_message failed: %s", exc)
        return {"error": "Failed to send message"}


@router.put("/chat/conversations/{conversation_id}/read")
async def mark_read(
    conversation_id: str,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    ids = _get_participant_ids(conversation_id)
    if not ids or current_user_id not in ids:
        return {"error": "Conversation not found or not a participant"}
    is_buyer = current_user_id == ids[0]
    unread_field = "buyer_unread" if is_buyer else "seller_unread"

    admin = get_supabase_admin()
    try:
        admin.table("messages").update({"read_at": datetime.now(timezone.utc).isoformat()}).eq(
            "conversation_id", conversation_id
        ).eq("read_at", None).neq("sender_id", current_user_id).execute()
        admin.table("conversations").update({unread_field: 0}).eq("id", conversation_id).execute()
        return {"status": "read"}
    except Exception as exc:
        logger.warning("mark_read failed: %s", exc)
        return {"error": "Failed to mark as read"}


@router.get("/chat/unread")
async def unread_count(
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return {"unread_count": _unread_for(current_user_id)}


# ── WebSocket ──────────────────────────────────────────────────────

_ws_clients: dict[str, list[WebSocket]] = {}


async def _broadcast(conversation_id: str, data: dict[str, Any]) -> None:
    cid = str(conversation_id)
    for ws in _ws_clients.get(cid, [])[:]:
        try:
            await ws.send_json(data)
        except Exception:
            try:
                _ws_clients[cid].remove(ws)
            except ValueError:
                pass


@router.websocket("/ws/chat/{conversation_id}")
async def chat_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        auth = json.loads(raw)
        token = auth.get("token", "")
        payload = _decode_auth_token(token)
        if not payload:
            await websocket.send_json({"error": "Invalid token"})
            await websocket.close()
            return

        user_id = payload.sub
        ids = _get_participant_ids(conversation_id)
        if not ids or user_id not in ids:
            await websocket.send_json({"error": "Not a participant"})
            await websocket.close()
            return

        cid = conversation_id
        _ws_clients.setdefault(cid, []).append(websocket)
        await websocket.send_json({"type": "connected", "conversation_id": cid})

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            content = data.get("content", "").strip()
            if not content or len(content) > 2000:
                continue

            admin = get_supabase_admin()
            is_buyer = user_id == ids[0]
            unread_field = "seller_unread" if is_buyer else "buyer_unread"

            msg_r = admin.table("messages").insert({
                "conversation_id": conversation_id,
                "sender_id": user_id,
                "content": content,
            }).execute()

            if msg_r.data:
                now_iso = datetime.now(timezone.utc).isoformat()
                admin.rpc("increment_unread", {
                    "p_field": unread_field,
                    "p_conversation_id": conversation_id,
                }).execute()
                admin.table("conversations").update({
                    "last_message": content[:100],
                    "last_message_at": now_iso,
                    "updated_at": now_iso,
                }).eq("id", conversation_id).execute()

                await _broadcast(conversation_id, {
                    "type": "new_message",
                    "message": msg_r.data[0],
                })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("chat_ws error: %s", exc)
    finally:
        cid = conversation_id
        if cid in _ws_clients:
            try:
                _ws_clients[cid].remove(websocket)
            except ValueError:
                pass
            if not _ws_clients[cid]:
                del _ws_clients[cid]
