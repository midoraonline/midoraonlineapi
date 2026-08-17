"""Pre-submit listing quality check.

Called from the merchant's product form. Reads title, description and the
uploaded image URLs, then asks Gemini whether the listing is ready to
publish. Fails open: on any AI/network error we return `ok: true` so a
Gemini outage never blocks the merchant.
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from core.security import get_current_user_id
from ai import service as ai_service
from ai.schemas import ListingQualityRequest, ListingQualityResponse

router = APIRouter()


@router.post("/quality-check", response_model=ListingQualityResponse)
async def check_quality(
    body: ListingQualityRequest,
    _user_id: Annotated[str, Depends(get_current_user_id)],
) -> ListingQualityResponse:
    result = await ai_service.check_listing_quality(
        title=body.title,
        description=body.description,
        image_urls=body.image_urls,
        category=body.category,
    )
    return ListingQualityResponse(**result)
