from __future__ import annotations

import logging
from typing import Any
from pydantic import BaseModel

from fastapi import APIRouter, Depends

from core.security import get_optional_user_id
from db.supabase import get_supabase_admin
from mail.send import send_feedback_admin_email
from mail.queue import get_admin_emails

logger = logging.getLogger(__name__)

router = APIRouter()

class FeedbackRequest(BaseModel):
    feedback_text: str

@router.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    current_user_id: str | None = Depends(get_optional_user_id),
) -> dict[str, Any]:
    """Submit platform feedback."""
    feedback_text = request.feedback_text.strip()
    if not feedback_text:
        return {"error": "Feedback text is required"}

    admin = get_supabase_admin()
    user_email = None

    try:
        # Get user email if authenticated
        if current_user_id:
            try:
                reporter_r = admin.table("users").select("email").eq("id", current_user_id).limit(1).execute()
                if reporter_r.data and reporter_r.data[0].get("email"):
                    user_email = reporter_r.data[0]["email"]
            except Exception:
                pass

        # Insert into platform_feedback
        r = admin.table("platform_feedback").insert({
            "user_id": current_user_id,
            "feedback_text": feedback_text,
        }).execute()

        # Notify admins
        try:
            admin_emails = get_admin_emails()
            if admin_emails:
                await send_feedback_admin_email(
                    admin_recipients=admin_emails,
                    user_email=user_email,
                    feedback_text=feedback_text,
                )
        except Exception as exc:
            logger.warning("Failed to send feedback notification email: %s", exc)

        return {"status": "success", "message": "Feedback submitted successfully"}
    except Exception as exc:
        logger.warning("submit_feedback failed: %s", exc)
        return {"error": "Failed to submit feedback"}
