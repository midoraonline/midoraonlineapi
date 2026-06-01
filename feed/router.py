from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi_cache.decorator import cache
from pydantic import BaseModel

from core.schemas import PaginationParams
from core.security import get_optional_user_id, get_current_user_id
from db.supabase import get_supabase_client
from shop.schemas import ProductResponse
from feed import service as feed_service
from feed.composite import get_home_feed

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


@router.get("/home")
@cache(expire=300)
async def home_feed(limit: int = Query(72, ge=1, le=200)) -> dict[str, Any]:
    """Composite endpoint: all 4 feeds with shop + boost data embedded.

    Returns algorithm, trending, premium, and fresh feeds in a single call.
    Shop details and boost status are batch-fetched and embedded so the
    frontend doesn't need N+1 round-trips.
    """
    return get_home_feed(limit=limit)


@router.get("/algorithm", response_model=list[ProductResponse])
@cache(expire=300)
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
@cache(expire=300)
async def get_latest_feed(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
):
    """
    Get the latest products ordered by creation/repost time.
    """
    return feed_service.get_latest_feed(client, limit=params.limit)
