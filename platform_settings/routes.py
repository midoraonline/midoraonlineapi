from fastapi import APIRouter, Response

from platform_settings.flags import all_media_limits, public_flags

router = APIRouter(tags=["settings"])


@router.get("/settings/public")
async def public_settings(response: Response) -> dict:
    """Non-sensitive feature switches. Safe to cache at the edge."""
    response.headers["Cache-Control"] = "public, s-maxage=30, stale-while-revalidate=120"
    data = all_media_limits()
    return {"flags": public_flags(), "limits": data["default"], "plan_limits": data["plans"]}
