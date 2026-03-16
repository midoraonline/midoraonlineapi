from typing import Annotated

from fastapi import APIRouter, Depends

from core.security import get_current_user_id
from ai import service as ai_service
from ai.schemas import RemoveBackgroundRequest, RemoveBackgroundResponse

router = APIRouter()


@router.post("/remove-background", response_model=RemoveBackgroundResponse)
async def remove_background(
    body: RemoveBackgroundRequest,
    user_id: str = Depends(get_current_user_id),
):
    url = ai_service.remove_background(
        image_url=body.image_url,
        image_base64=body.image_base64,
    )
    return RemoveBackgroundResponse(image_url=url)
