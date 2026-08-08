"""POST /moderation/listings — enqueue a listing for background moderation."""
from __future__ import annotations

from fastapi import APIRouter, status

from .. import service
from ..schemas import SubmitListingRequest, SubmitListingResponse

router = APIRouter()


@router.post(
    "/listings",
    response_model=SubmitListingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_listing(payload: SubmitListingRequest) -> SubmitListingResponse:
    """Enqueue and return immediately (202). The cron drain does the work.

    Deliberately NOT using FastAPI `BackgroundTasks` — Vercel serverless
    kills the function process as soon as the response ships, so any
    `background_tasks.add_task(...)` would silently be dropped. See
    docs/UI_STACKING.md / mail/queue.py for the same pattern applied to
    email sending.
    """
    row = service.enqueue(payload)
    return SubmitListingResponse(id=row.id, status=row.status)
