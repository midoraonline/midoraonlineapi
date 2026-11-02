from typing import Annotated, Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, Query, Response

from core.schemas import PaginationParams
from core.security import get_optional_user_id
from shop.schemas import ProductResponse
from feed import service as feed_service
from feed.catalog import ListingFilters, listing_filters
from feed.composite import get_home_feed

router = APIRouter(prefix="/feed", tags=["feed"])

_PUBLIC_CACHE_HEADER = "public, s-maxage=300, stale-while-revalidate=60"


def _split_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for chunk in raw.split(","):
        pid = chunk.strip()
        if pid and pid not in seen and len(pid) <= 64:
            seen.add(pid)
            out.append(pid)
    return out[:500]


def _decode_cursor(raw: str | None) -> tuple[int | None, str | None]:
    parts = (raw or "").strip().split(":")
    version = None
    if len(parts) == 4 and parts[0] == "v" and 0 < len(parts[1]) <= 32:
        version, parts = parts[1], parts[2:]
    if len(parts) != 2 or parts[0] != "p":
        return None, None
    try:
        page = int(parts[1])
    except ValueError:
        return None, None
    return (page, version) if page >= 1 else (None, None)


@router.get("/home")
async def home_feed(
    response: Response,
    filters: Annotated[ListingFilters, Depends(listing_filters)],
    limit: int = Query(40, ge=1, le=200),
    page: int = Query(1, ge=1),
    cursor: str | None = Query(None, description="Continuation cursor: v:<version>:p:<page> or p:<page>."),
    exclude_ids: str | None = Query(None, description="Legacy. Ignored when cursor is present."),
    user_id: str | None = Depends(get_optional_user_id),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, Any]:
    session = session_id_header or session_id
    cursor_page, version = _decode_cursor(cursor)
    effective_page = cursor_page or page
    excluded = [] if cursor_page is not None else _split_ids(exclude_ids)
    if not user_id:
        response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=120"
    return get_home_feed(
        limit=limit,
        page=effective_page,
        user_id=user_id,
        exclude_ids=excluded,
        session_id=session,
        filters=filters,
        version=version,
    )


@router.get("/locations")
async def popular_locations(
    response: Response,
    limit: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    from db.supabase import get_supabase_admin
    from feed.locations import popular_locations as load

    response.headers["Cache-Control"] = _PUBLIC_CACHE_HEADER
    return {"items": load(get_supabase_admin(), limit)}


@router.get("/cron/refresh")
@router.post("/cron/refresh")
async def refresh_ranking(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    from media.routes import authorize_cron

    authorize_cron(authorization)
    return feed_service.rebuild_ranking()


@router.get("/latest", response_model=list[ProductResponse])
async def get_latest_feed(
    response: Response,
    params: Annotated[PaginationParams, Depends()],
    filters: Annotated[ListingFilters, Depends(listing_filters)],
):
    response.headers["Cache-Control"] = _PUBLIC_CACHE_HEADER
    return feed_service.get_latest_feed(limit=params.limit, page=params.page, filters=filters)
