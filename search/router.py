from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from supabase import Client

from core.schemas import PaginationParams
from core.security import get_current_user_id, get_optional_user_id
from db.supabase import get_supabase_client
from search import service as search_service

router = APIRouter(prefix="/search", tags=["search"])


class SearchLogBody(BaseModel):
    query: str = Field(..., min_length=2, max_length=200)


@router.get("/products")
async def search_products(
    client: Annotated[Client, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    category: str | None = Query(None, description="Optional category filter"),
    user_id: str | None = Depends(get_optional_user_id),
    log: bool = Query(True, description="Record query in search_history"),
) -> dict[str, Any]:
    """Semantic product search using stored embeddings.

    Falls back to keyword matching when embeddings are unavailable.
    Authenticated searches are logged to ``search_history`` for personalization
    and trending analytics.
    """
    return search_service.search_products(
        client,
        q,
        page=params.page,
        limit=params.limit,
        category=category,
        user_id=user_id,
        log=log,
    )


@router.get("/trending")
async def trending_searches(
    client: Annotated[Client, Depends(get_supabase_client)],
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(30, ge=1, le=90, description="Look-back window in days"),
) -> dict[str, Any]:
    """Most searched queries over the recent window."""
    items = search_service.get_trending_searches(client, limit=limit, days=days)
    return {"items": items, "days": days}


@router.get("/recent")
async def recent_searches(
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(10, ge=1, le=30),
) -> dict[str, Any]:
    """Current user's recent unique search queries."""
    items = search_service.get_recent_searches(client, user_id, limit=limit)
    return {"items": items}


@router.post("/log")
async def log_search_query(
    body: SearchLogBody,
    client: Annotated[Client, Depends(get_supabase_client)],
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Manually log a search query (e.g. from autocomplete selection)."""
    search_service.log_search(client, body.query, user_id=user_id)
    return {"status": "ok"}
