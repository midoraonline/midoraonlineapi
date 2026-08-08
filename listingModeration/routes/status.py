"""GET /moderation/listings/{id} and GET /moderation/listings — status polling."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from .. import service
from ..schemas import ModerationRow, ModerationStatus

router = APIRouter()


@router.get("/listings/{listing_id}", response_model=ModerationRow)
def get_status(listing_id: UUID) -> ModerationRow:
    row = service.get_by_id(listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="moderation row not found")
    return row


@router.get("/listings", response_model=list[ModerationRow])
def list_by_status(
    status: Optional[ModerationStatus] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ModerationRow]:
    """List rows by status. Primarily for a reviewer UI on `needs_review`."""
    return service.list_by_status(status or ModerationStatus.NEEDS_REVIEW, limit=limit)
