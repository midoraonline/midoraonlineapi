from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ranking.boost_service import list_active_boost_plans, purchase_boost, get_active_boost_for_listing
from core.security import get_optional_user_id

router = APIRouter()


@router.get("")
async def get_boost_plans() -> list[dict[str, Any]]:
    """List active boost plans available for purchase."""
    return list_active_boost_plans()


@router.get("/listing/{listing_id}/active")
async def get_listing_active_boost(listing_id: str) -> dict[str, Any] | None:
    """Get active boost for a listing."""
    boost = get_active_boost_for_listing(listing_id)
    if boost:
        return boost
    return None
