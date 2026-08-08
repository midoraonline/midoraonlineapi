from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ModerationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class SubmitListingRequest(BaseModel):
    """Payload for enqueueing a listing for moderation.

    `product_id` is optional: pass it when moderating an existing product
    (the pipeline writes the final status back to `products.status`). Leave
    null when pre-moderating drafts before insert.
    """
    product_id: Optional[UUID] = None
    seller_id: Optional[UUID] = None
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    image_urls: list[str] = Field(default_factory=list, max_length=32)


class SubmitListingResponse(BaseModel):
    id: UUID
    status: ModerationStatus


class ModerationRow(BaseModel):
    id: UUID
    product_id: Optional[UUID] = None
    seller_id: Optional[UUID] = None
    title: str
    description: str
    image_urls: list[str]
    status: ModerationStatus
    reason: Optional[str] = None
    scores: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    error: Optional[str] = None
    created_at: datetime
    processing_started_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


class ModerationDecision(BaseModel):
    """Internal result of running the pipeline on a single row."""
    status: ModerationStatus
    reason: Optional[str] = None
    scores: dict[str, Any] = Field(default_factory=dict)


class DrainResponse(BaseModel):
    reclaimed: int
    processed: int
    approved: int
    rejected: int
    needs_review: int
    failed: int
