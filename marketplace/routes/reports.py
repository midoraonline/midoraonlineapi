from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from core.security import get_current_user_id
from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter()


REPORT_REASONS = [
    "Fake or counterfeit item",
    "Scam or fraud",
    "Prohibited item",
    "Wrong category",
    "Spam or duplicate",
    "Misleading description",
    "Other",
]


@router.post("/products/{product_id}/reports")
async def report_product(
    product_id: str,
    reason: str,
    description: str | None = None,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Report a product listing."""
    if reason not in REPORT_REASONS:
        return {"error": f"Invalid reason. Must be one of: {', '.join(REPORT_REASONS)}"}

    admin = get_supabase_admin()
    try:
        existing = (
            admin.table("product_reports")
            .select("id")
            .eq("product_id", product_id)
            .eq("reporter_id", current_user_id)
            .eq("resolved", False)
            .execute()
        )
        if existing.data:
            return {"status": "already_reported", "message": "You have already reported this listing"}

        r = admin.table("product_reports").insert({
            "product_id": product_id,
            "reporter_id": current_user_id,
            "reason": reason,
            "description": description,
        }).execute()

        if r.data:
            from ranking.service import calculate_listing_score
            calculate_listing_score(product_id)

        return r.data[0] if r.data else {"status": "reported"}
    except Exception as exc:
        logger.warning("report_product failed: %s", exc)
        return {"error": "Failed to submit report"}
