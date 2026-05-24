from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi_cache.decorator import cache
from pydantic import BaseModel

from core.schemas import PaginationParams
from core.security import get_optional_user_id, get_current_user_id
from db.supabase import get_supabase_client
from shop.schemas import ProductResponse
from feed import service as feed_service

router = APIRouter(prefix="/feed", tags=["feed"])

class SearchQuery(BaseModel):
    query: str

@router.post("/search-history")
async def log_search(
    body: SearchQuery,
    client: Annotated[any, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
):
    """Log a search query to tailor the algorithm feed."""
    q = body.query.strip()
    if len(q) >= 2:
        client.table("search_history").insert({"user_id": user_id, "query": q}).execute()
    return {"status": "ok"}



@router.get("/algorithm", response_model=list[ProductResponse])
@cache(expire=60)
async def get_algorithm_feed(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    Get a personalized feed of products based on user interactions.
    """
    return feed_service.get_algorithm_feed(client, user_id=user_id, limit=params.limit)


@router.get("/latest", response_model=list[ProductResponse])
@cache(expire=60)
async def get_latest_feed(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
):
    """
    Get the latest products ordered by creation/repost time.
    """
    return feed_service.get_latest_feed(client, limit=params.limit)
