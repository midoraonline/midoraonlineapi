from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


VerificationStatus = Literal["unverified", "pending", "verified", "rejected"]


class VerificationSubmitRequest(BaseModel):
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class VerificationDecisionRequest(BaseModel):
    notes: str | None = None


class VerificationResponse(BaseModel):
    id: str
    shop_id: str
    status: VerificationStatus
    requested_at: str | None = None
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
