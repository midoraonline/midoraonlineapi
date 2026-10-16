from fastapi import APIRouter, Depends, HTTPException

from ai.background import (
    BackgroundRemovalFailed,
    BackgroundRemovalUnavailable,
    remove_background_to_url,
)
from ai.schemas import RemoveBackgroundRequest, RemoveBackgroundResponse
from core.security import get_current_user_id

router = APIRouter()


@router.post("/remove-background", response_model=RemoveBackgroundResponse)
async def remove_background(
    body: RemoveBackgroundRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Remove a photo's background server-side at full resolution. Returns the new photo URL."""
    if not body.image_url:
        raise HTTPException(status_code=422, detail={"detail": "image_url is required.", "code": "image_url_required"})
    try:
        url = await remove_background_to_url(body.image_url, user_id=user_id, bg_color=body.bg_color)
    except BackgroundRemovalUnavailable as exc:
        raise HTTPException(status_code=501, detail={"detail": str(exc), "code": "bg_removal_unavailable"}) from exc
    except BackgroundRemovalFailed as exc:
        raise HTTPException(status_code=502, detail={"detail": str(exc), "code": "bg_removal_failed"}) from exc
    return RemoveBackgroundResponse(image_url=url)
