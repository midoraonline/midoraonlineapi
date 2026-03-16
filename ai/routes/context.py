from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from db.supabase import get_supabase_client
from core.security import get_current_user_id

router = APIRouter()


@router.get("/{shop_id}/ai-context")
async def list_ai_context(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    r = client.table("shop_ai_context").select("*").eq("shop_id", shop_id).execute()
    return r.data or []


@router.post("/{shop_id}/ai-context")
async def create_ai_context(
    shop_id: str,
    body: dict,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    from ai.schemas import AIContextCreate
    payload = body if isinstance(body, dict) else body.model_dump()
    r = client.table("shop_ai_context").insert({
        "shop_id": shop_id,
        "context_type": payload.get("context_type", "policy"),
        "content": payload.get("content", ""),
    }).execute()
    if not r.data or len(r.data) == 0:
        raise HTTPException(status_code=400, detail="Failed to create context")
    return r.data[0]
