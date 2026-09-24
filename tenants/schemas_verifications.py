from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


VerificationStatus = Literal[
    "unverified", "submitted", "pending", "verified", "rejected"
]


class DocumentUpload(BaseModel):
    url: str
    # identity: national_id_*, passport, driving_permit, selfie
    # business: business_cert, shop_photo, business_reg, tax_doc, tin
    # professional: professional_cred, professional_license
    type: str
    label: str


class VerificationSubmitRequest(BaseModel):
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    documents: list[DocumentUpload] | None = None
    submitted_phone: str | None = None
    submitted_whatsapp: str | None = None
    submitted_location: str | None = None
    # 2=Identity, 3=Business, 4=Professional
    stage: int = 2
    # False = Stage-1 capture (store docs, no admin queue). True = enter review queue.
    request_review: bool = False


class VerificationDecisionRequest(BaseModel):
    notes: str | None = None


class VerificationRequestReviewBody(BaseModel):
    stage: int = Field(default=2, ge=2, le=4)
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
    submitted_docs: list[dict[str, Any]] | None = None
    submitted_phone: str | None = None
    submitted_whatsapp: str | None = None
    submitted_location: str | None = None
    shop_duration_days: int = 0
    current_stage: int = 1
    badges: list[str] = []
    stage2_status: VerificationStatus = "unverified"
    stage3_status: VerificationStatus = "unverified"
    stage4_status: VerificationStatus = "unverified"
