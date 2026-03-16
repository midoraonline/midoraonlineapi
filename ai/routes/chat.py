from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from db.supabase import get_supabase_client
from core.security import get_optional_user_id
from ai import service as ai_service
from ai.schemas import ChatSessionCreate

router = APIRouter()


@router.post("/sessions")
async def create_chat_session(
    body: ChatSessionCreate,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str | None = Depends(get_optional_user_id),
):
    intent = body.intent
    shop_id = body.shop_id
    if intent == "create_shop":
        shop_id = None
    elif not shop_id:
        raise HTTPException(status_code=400, detail="shop_id required (or intent=create_shop)")
    payload = {"customer_id": user_id, "shop_id": shop_id}
    if intent:
        payload["intent"] = intent
    r = client.table("chat_sessions").insert(payload).execute()
    if not r.data or len(r.data) == 0:
        raise HTTPException(status_code=400, detail="Failed to create session")
    return r.data[0]


@router.get("/sessions")
async def list_chat_sessions(
    client: Annotated[any, Depends(get_supabase_client)],
    shop_id: str | None = None,
    user_id: str | None = Depends(get_optional_user_id),
):
    q = client.table("chat_sessions").select("*")
    if shop_id:
        q = q.eq("shop_id", shop_id)
    if user_id:
        q = q.eq("customer_id", user_id)
    r = q.execute()
    return r.data or []


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: dict,
    client: Annotated[any, Depends(get_supabase_client)],
):
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    sess = client.table("chat_sessions").select("shop_id, intent").eq("id", session_id).execute()
    if not sess.data or len(sess.data) == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    shop_id = sess.data[0].get("shop_id")
    intent = sess.data[0].get("intent")

    if intent == "create_shop":
        # Build conversation history and call create-shop AI
        hist = client.table("chat_messages").select("sender_type, message").eq("session_id", session_id).order("created_at").execute()
        messages = list(hist.data or [])
        messages.append({"sender_type": "customer", "message": message})
        reply, suggested_shop = ai_service.chat_create_shop(messages)
    else:
        ctx = client.table("shop_ai_context").select("content").eq("shop_id", shop_id).execute()
        shop_context = "\n".join(row.get("content", "") for row in (ctx.data or []))
        reply = ai_service.chat_with_context(shop_context, None, message)
        suggested_shop = None

    client.table("chat_messages").insert({
        "session_id": session_id,
        "sender_type": "customer",
        "message": message,
    }).execute()
    client.table("chat_messages").insert({
        "session_id": session_id,
        "sender_type": "ai_concierge",
        "message": reply,
    }).execute()
    return {"message": reply, "sender_type": "ai_concierge", "suggested_shop": suggested_shop}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
  client: Annotated[any, Depends(get_supabase_client)],
):
    r = client.table("chat_messages").select("*").eq("session_id", session_id).order("created_at").execute()
    return r.data or []
