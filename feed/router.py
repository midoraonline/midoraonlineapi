from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Header, Query, Response
from supabase import Client

from core.schemas import PaginationParams
from core.security import get_optional_user_id
from db.supabase import get_supabase_client
from shop.schemas import ProductResponse
from feed import service as feed_service
from feed.composite import get_home_feed

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
        description="Legacy. Prefer cursor. Ignored when cursor is present.",
    ),
    category: str | None = Query(
        None,
        description=(
            "Optional category label or slug. Parent labels expand to their "
            "subcategories. Filters candidates server-side so pagination stays "
            "in-category."
        ),
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

    Pagination:
      * First paint: `GET /feed/home?limit=36` (no cursor, no exclude_ids).
      * Load-more: `GET /feed/home?limit=36&cursor=p:2` using `next_cursor`.
      * `exclude_ids` is legacy; ignored when `cursor` is present so clients
        cannot double-skip a page.
    """
    session = session_id_header or session_id
    cursor_page = _decode_page_cursor(cursor)
    effective_page = cursor_page or page
    excluded = [] if cursor_page is not None else _split_ids(exclude_ids)
    if not user_id:
        # Anonymous home feed is safe to edge-cache briefly.
        response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=120"
    return get_home_feed(
        limit=limit,
        page=effective_page,
        user_id=user_id,
        exclude_ids=excluded,
        session_id=session,
        category=category,
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
