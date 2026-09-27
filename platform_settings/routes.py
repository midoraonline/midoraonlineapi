from fastapi import APIRouter, Response

from platform_settings.flags import public_flags

router = APIRouter(tags=["settings"])


@router.get("/settings/public")
async def public_settings(response: Response) -> dict:
    """Non-sensitive feature switches. Safe to cache at the edge."""
    response.headers["Cache-Control"] = "public, s-maxage=30, stale-while-revalidate=120"
    return {"flags": public_flags()}
