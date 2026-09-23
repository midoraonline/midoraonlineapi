from fastapi import APIRouter, Depends, HTTPException

from core.security import get_current_user_id
from ai import service as ai_service
from ai.schemas import RemoveBackgroundRequest, RemoveBackgroundResponse

router = APIRouter()


@router.post("/remove-background", response_model=RemoveBackgroundResponse)
async def remove_background(
    body: RemoveBackgroundRequest,
    user_id: str = Depends(get_current_user_id),
):
    try:
        url = ai_service.remove_background(
            image_url=body.image_url,
            image_base64=body.image_base64,
        )
    except ai_service.AIUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return RemoveBackgroundResponse(image_url=url)
