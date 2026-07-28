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


def _decode_page_cursor(raw: str | None) -> int | None:
    if not raw:
        return None
    v = raw.strip()
    if not v.startswith("p:"):
        return None
    try:
        page = int(v[2:])
    except ValueError:
        return None
    return page if page >= 1 else None


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
    feed_service.invalidate_user_feed_cache(client, user_id)
    return {"status": "ok"}


@router.get("/home")
async def home_feed(
    response: Response,
    limit: int = Query(72, ge=1, le=200),
    page: int = Query(1, ge=1),
    cursor: str | None = Query(
        None,
        description="Continuation cursor (format: p:<page>). When present it overrides page.",
    ),
    exclude_ids: str | None = Query(
        None,
        description="Comma-separated listing IDs already shown on this session.",
    ),
    user_id: str | None = Depends(get_optional_user_id),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, Any]:
    """Composite endpoint: all 4 feeds with shop + boost data embedded.

    Personalized users (authenticated):
      * Rank once, store IDs in `user_feed_cache` for **1 hour TTL**.
      * Within TTL: hydrate cards from cached order (no re-score).
      * After TTL: full algorithm runs again and refreshes the cache.
      * Load-more uses `exclude_ids` only; does not trigger re-score.

    Exclusion (load-more only):
      * Initial SSR / first paint: do NOT send `exclude_ids` — return top ranks.
      * Load-more / soft refresh: send `exclude_ids` of cards already on screen.
        Server drops those IDs then returns the next `limit` from the head of
        the remaining ranked list (page/cursor ignored when exclude is set).
      * Fatigue (≥3 impressions / 48h) only applies on continuation, not page 1.
    """
    session = session_id_header or session_id
    cursor_page = _decode_page_cursor(cursor)
    effective_page = cursor_page or page
    if not user_id:
        # Anonymous home feed is safe to edge-cache briefly.
        response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=120"
    return get_home_feed(
        limit=limit,
        page=effective_page,
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
    """Personalized feed.

    For authenticated users, ranked IDs are cached for **1 hour** (`user_feed_cache`).
    Reloads and load-more reuse that ranking until the TTL expires, then the
    algorithm is recalculated once and written back.

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
