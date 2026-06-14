import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from db.supabase import get_supabase_client
from core.security import get_optional_user_id
from ai import service as ai_service
from ai.schemas import ChatSessionCreate

router = APIRouter()


class MidoraChatRequest(BaseModel):
    message: str


class MidoraChatResponse(BaseModel):
    message: str


def _rate_limit_error(exc: ai_service.AIRateLimitError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"AI is temporarily unavailable due to rate limits. Please retry in {exc.retry_after} seconds.",
        headers={"Retry-After": str(exc.retry_after)},
    )


@router.post("/midora", response_model=MidoraChatResponse)
async def midora_info_chat(body: MidoraChatRequest) -> MidoraChatResponse:
    """Midora Online info bot (no sessions, no shop context)."""
    try:
        reply = await ai_service.chat_midora_info(body.message)
    except ai_service.AIUnavailableError:
        raise HTTPException(status_code=503, detail="AI is not configured.")
    except ai_service.AIRateLimitError as exc:
        raise _rate_limit_error(exc)
    return MidoraChatResponse(message=reply)


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
    r = await asyncio.to_thread(
        lambda: client.table("chat_sessions").insert(payload).execute()
    )
    if not r.data or len(r.data) == 0:
        raise HTTPException(status_code=400, detail="Failed to create session")
    return r.data[0]


@router.get("/sessions")
async def list_chat_sessions(
    client: Annotated[any, Depends(get_supabase_client)],
    shop_id: str | None = None,
    user_id: str | None = Depends(get_optional_user_id),
):
    def _query():
        q = client.table("chat_sessions").select("*")
        if shop_id:
            q = q.eq("shop_id", shop_id)
        if user_id:
            q = q.eq("customer_id", user_id)
        return q.execute()

    r = await asyncio.to_thread(_query)
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

    sess = await asyncio.to_thread(
        lambda: client.table("chat_sessions")
        .select("shop_id, intent")
        .eq("id", session_id)
        .execute()
    )
    if not sess.data or len(sess.data) == 0:
        raise HTTPException(status_code=404, detail="Session not found")

    shop_id = sess.data[0].get("shop_id")
    intent = sess.data[0].get("intent")

    hist = await asyncio.to_thread(
        lambda: client.table("chat_messages")
        .select("sender_type, message")
        .eq("session_id", session_id)
        .order("created_at")
        .execute()
    )
    messages = list(hist.data or [])
    messages.append({"sender_type": "customer", "message": message})

    try:
        if intent == "create_shop":
            reply, suggested_shop = await ai_service.chat_create_shop(messages)
        else:
            ctx = await asyncio.to_thread(
                lambda: client.table("shop_ai_context")
                .select("content")
                .eq("shop_id", shop_id)
                .execute()
            )
            shop_context = "\n".join(row.get("content", "") for row in (ctx.data or []))
            reply = await ai_service.chat_with_shop_tools(
                shop_id=str(shop_id),
                shop_context=shop_context,
                messages=messages,
            )
            suggested_shop = None
    except ai_service.AIUnavailableError:
        raise HTTPException(status_code=503, detail="AI is not configured.")
    except ai_service.AIRateLimitError as exc:
        raise _rate_limit_error(exc)

    await asyncio.to_thread(
        lambda: client.table("chat_messages").insert({
            "session_id": session_id,
            "sender_type": "customer",
            "message": message,
        }).execute()
    )
    await asyncio.to_thread(
        lambda: client.table("chat_messages").insert({
            "session_id": session_id,
            "sender_type": "ai_concierge",
            "message": reply,
        }).execute()
    )
    return {"message": reply, "sender_type": "ai_concierge", "suggested_shop": suggested_shop}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
):
    r = await asyncio.to_thread(
        lambda: client.table("chat_messages")
        .select("*")
        .eq("session_id", session_id)
        .order("created_at")
        .execute()
    )
    return r.data or []
