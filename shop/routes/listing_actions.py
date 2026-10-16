from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.security import get_current_user_id
from db.supabase import get_supabase_admin
from shop import listing_actions as svc

router = APIRouter()


class BoostRequest(BaseModel):
    package_id: str = Field(..., min_length=1)
    use_allowance: bool = True


@router.get("/{listing_id}/promote")
def promote_options(listing_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Boost packages, free boosts left this month, active boost and repost limits."""
    return svc.promote_options(get_supabase_admin(), listing_id, user_id)


@router.post("/{listing_id}/boost")
def boost(listing_id: str, body: BoostRequest, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return svc.boost_listing(get_supabase_admin(), listing_id, user_id, body.package_id, body.use_allowance)


@router.post("/{listing_id}/repost")
def repost(listing_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return svc.repost_listing(get_supabase_admin(), listing_id, user_id)


@router.get("/{listing_id}/feed-position")
def feed_position(listing_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Live 1-based position in the public home feed and in its category."""
    return svc.feed_position(get_supabase_admin(), listing_id, user_id)
