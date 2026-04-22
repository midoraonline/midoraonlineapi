from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.authz import ensure_shop_owner
from db.supabase import get_supabase_client
from core.security import get_current_user_id

router = APIRouter()


@router.get("/{shop_id}/ai-context")
async def list_ai_context(
    shop_id: str,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    r = client.table("shop_ai_context").select("*").eq("shop_id", shop_id).execute()
    return r.data or []


@router.post("/{shop_id}/ai-context")
async def create_ai_context(
    shop_id: str,
    body: dict,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    payload = body if isinstance(body, dict) else body.model_dump()
    r = client.table("shop_ai_context").insert({
        "shop_id": shop_id,
        "context_type": payload.get("context_type", "policy"),
        "content": payload.get("content", ""),
    }).execute()
    if not r.data or len(r.data) == 0:
        raise HTTPException(status_code=400, detail="Failed to create context")
    return r.data[0]
