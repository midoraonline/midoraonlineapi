from typing import Annotated, Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from core.rate_limit import RateLimitDemand
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
    refresh: bool = Query(False, description="Start a new Home session (pull to refresh / new listings)."),
    area: str | None = Query(None, max_length=60, description="Browsing area preference, e.g. kampala."),
    neutral: bool = Query(False, description="Start a session with no chosen area or history (Find in feed)."),
    user_id: str | None = Depends(get_optional_user_id),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, Any]:
    from feed.location import normalize_area
    from feed.sessions import key_for

    session = session_id_header or session_id
    cursor_page, version = _decode_cursor(cursor)
    effective_page = cursor_page or page
    excluded = [] if cursor_page is not None else _split_ids(exclude_ids)
    if key_for(user_id, session) is not None:
        response.headers["Cache-Control"] = "private, no-store"
    elif not user_id:
        response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=120"
    return get_home_feed(
        limit=limit,
        page=effective_page,
        user_id=user_id,
        exclude_ids=excluded,
        session_id=session,
        filters=filters,
        version=version,
        refresh=refresh,
        neutral=neutral,
        area=normalize_area(area) if area is not None else None,
    )


@router.get("/home/new")
async def home_new_count(
    response: Response,
    user_id: str | None = Depends(get_optional_user_id),
    session_id: str | None = Cookie(default=None, alias="midora_session_id"),
    session_id_header: str | None = Header(default=None, alias="X-Midora-Session"),
) -> dict[str, int]:
    from db.supabase import get_supabase_admin

    response.headers["Cache-Control"] = "private, no-store"
    count = feed_service.new_listing_count(
        get_supabase_admin(), user_id=user_id, session_id=session_id_header or session_id
    )
    return {"count": count}


class DemandRequest(BaseModel):
    need: str = Field(..., min_length=3, max_length=200)
    category: str | None = Field(None, max_length=80)
    area: str | None = Field(None, max_length=60)
    notify: bool = False


@router.post("/demand", status_code=202)
async def request_demand(
    body: DemandRequest,
    _: RateLimitDemand,
    user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, Any]:
    from feed.demand import record_request
    from feed.location import normalize_area

    record_request(
        actor_id=user_id,
        need=body.need,
        category=body.category,
        area=normalize_area(body.area),
        notify=body.notify,
    )
    return {"ok": True, "alerts": bool(body.notify and user_id)}


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
    result: dict[str, Any] = dict(feed_service.rebuild_ranking())
    from db.supabase import get_supabase_admin
    from feed.seller_nudges import run_stock_nudges

    result["stock_nudges"] = run_stock_nudges(get_supabase_admin())
    return result


@router.get("/latest", response_model=list[ProductResponse])
async def get_latest_feed(
    response: Response,
    params: Annotated[PaginationParams, Depends()],
    filters: Annotated[ListingFilters, Depends(listing_filters)],
):
    response.headers["Cache-Control"] = _PUBLIC_CACHE_HEADER
    return feed_service.get_latest_feed(limit=params.limit, page=params.page, filters=filters)
