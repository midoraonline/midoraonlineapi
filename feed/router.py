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
    from search.service import log_search

    log_search(client, body.query, user_id=user_id)
    return {"status": "ok"}


@router.get("/home")
async def home_feed(
    limit: int = Query(72, ge=1, le=200),
    page: int = Query(1, ge=1),
    user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, Any]:
    """Composite endpoint: all 4 feeds with shop + boost data embedded.

    Returns algorithm, trending, premium, and fresh feeds in a single call.
    Shop details and boost status are batch-fetched and embedded so the
    frontend doesn't need N+1 round-trips.

    When a user is authenticated the algorithm feed is personalised based
    on viewed/liked categories, search history, and engagement signals.

    Supports pagination via `page` and `limit`. The main algorithm feed
    returns `limit` products; other sub-feeds are fixed-size.
    """
    return get_home_feed(limit=limit, page=page, user_id=user_id)


@router.get("/algorithm", response_model=list[ProductResponse])
async def get_algorithm_feed(
    client: Annotated[any, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    user_id: str | None = Depends(get_optional_user_id),
    page: int = Query(1, ge=1, description="Page number for paginated feed (cached in-process)."),
):
    """
    Get a personalized feed of products based on user interactions.

    Boosted products are always pinned to the top of the feed (Tier 1),
    ordered by the user's personal preference vector. Organic products
    follow immediately after (Tier 2), also ordered by personal score.

    Re-scored on every request from live user signals (likes, views,
    saves, follows, search history). Not cached.
    """
    return feed_service.get_algorithm_feed(
        client, user_id=user_id, page=page, limit=params.limit
    )


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
