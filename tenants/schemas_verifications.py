from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


VerificationStatus = Literal["unverified", "pending", "verified", "rejected"]


class DocumentUpload(BaseModel):
    url: str
    type: str  # "national_id_front", "national_id_back", "selfie", "business_cert"
    label: str


class VerificationSubmitRequest(BaseModel):
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    documents: list[DocumentUpload] | None = None
    submitted_phone: str | None = None
    submitted_whatsapp: str | None = None
    submitted_location: str | None = None


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
    submitted_docs: list[dict[str, Any]] | None = None
    submitted_phone: str | None = None
    submitted_whatsapp: str | None = None
    submitted_location: str | None = None
    shop_duration_days: int = 0
