from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from supabase import Client

from core.schemas import PaginationParams
from core.security import get_optional_user_id, get_current_user_id
from db.supabase import get_supabase_client
from shop.schemas import ProductResponse
from feed import service as feed_service
from feed.composite import get_home_feed
from feed import impressions as feed_impressions

router = APIRouter(prefix="/feed", tags=["feed"])

# Shared Cache-Control policy for public list endpoints. Vercel's edge caches
# public responses with `s-maxage`, so we get shared caching without any
# in-process store (which would be per-invocation on serverless anyway).
_PUBLIC_CACHE_HEADER = "public, s-maxage=300, stale-while-revalidate=60"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_ids(raw: str | None) -> list[str]:
    """Comma-separated UUID list -> deduped list. Silently ignores garbage."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for chunk in raw.split(","):
        pid = chunk.strip()
        if pid and pid not in seen and len(pid) <= 64:
            seen.add(pid)
            out.append(pid)
    return out[:500]  # hard cap — protects URL / header size


class SearchQuery(BaseModel):
    query: str


class ImpressionItem(BaseModel):
    listing_id: str = Field(min_length=1, max_length=64)
    pool: str | None = None
    position: int | None = None


class ImpressionBatch(BaseModel):
    items: list[ImpressionItem] = Field(default_factory=list, max_length=200)
    session_id: str | None = Field(default=None, max_length=128)
    device_hash: str | None = Field(default=None, max_length=128)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/search-history")
async def log_search(
    body: SearchQuery,
    client: Annotated[Client, Depends(get_supabase_client)],
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
    exclude_ids: str | None = Query(
        None,
        description="Comma-separated listing IDs already shown on this session.",
    ),
    user_id: str | None = Depends(get_optional_user_id),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, Any]:
    """Composite endpoint: all 4 feeds with shop + boost data embedded.

    Pagination + de-duplication:
      * `page` / `limit` slice the scored feed.
      * `exclude_ids` (client-tracked in-memory) hides items already
        rendered during this browsing session so subsequent pages never
        repeat content the user just scrolled past.
      * Fatigue suppression (server-side): listings shown >=3 times in the
        last 48h are also filtered out — per authenticated user OR per
        anonymous session cookie.
    """
    session = session_id_header or session_id
    return get_home_feed(
        limit=limit,
        page=page,
        user_id=user_id,
        exclude_ids=_split_ids(exclude_ids),
        session_id=session,
    )


@router.get("/algorithm", response_model=list[ProductResponse])
async def get_algorithm_feed(
    client: Annotated[Client, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
    user_id: str | None = Depends(get_optional_user_id),
    page: int = Query(1, ge=1, description="Page number for paginated feed."),
    exclude_ids: str | None = Query(None),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
):
    """Personalized feed. Re-scored on every request; never cached.

    Accepts `exclude_ids` for client-driven pagination de-duplication and
    honours the same fatigue rules as `/feed/home`.
    """
    session = session_id_header or session_id
    return feed_service.get_algorithm_feed(
        client,
        user_id=user_id,
        page=page,
        limit=params.limit,
        exclude_ids=_split_ids(exclude_ids),
        session_id=session,
    )


@router.get("/latest", response_model=list[ProductResponse])
async def get_latest_feed(
    response: Response,
    client: Annotated[Client, Depends(get_supabase_client)],
    params: Annotated[PaginationParams, Depends()],
):
    """Latest products. Public + cache-friendly via Cache-Control (edge cache)."""
    response.headers["Cache-Control"] = _PUBLIC_CACHE_HEADER
    return feed_service.get_latest_feed(client, limit=params.limit)


# ---------------------------------------------------------------------------
# Impression tracking — batched writes from the client
# ---------------------------------------------------------------------------

@router.post("/impressions")
async def record_impressions(
    body: ImpressionBatch,
    user_id: str | None = Depends(get_optional_user_id),
    session_cookie: str | None = Cookie(default=None, alias="midora_session_id"),
    session_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, int]:
    """Persist a batch of viewport-visible listing impressions.

    Called by `useImpressionTracker` on the client. Anonymous callers are
    identified by `session_id` (cookie or `X-Midora-Session` header). A
    10-minute cooldown per (viewer, listing) prevents duplicate rows when
    the same card re-enters the viewport during scrolling.
    """
    session_id = body.session_id or session_header or session_cookie
    count = feed_impressions.record_impressions(
        [item.model_dump() for item in body.items],
        buyer_id=user_id,
        session_id=session_id,
        device_hash=body.device_hash,
    )
    return {"recorded": count}
