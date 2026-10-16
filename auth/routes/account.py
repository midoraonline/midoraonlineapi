from fastapi import APIRouter, Depends, HTTPException, Response
import logging

from pydantic import BaseModel, Field

from auth.account_deletion import DeletionError, delete_account
from auth.cookies import clear_auth_cookies
from core.rate_limit import RateLimitOtp
from core.security import get_current_user_id

router = APIRouter()


DELETION_REASONS = {
    "not_useful",
    "too_many_messages",
    "privacy",
    "found_alternative",
    "selling_elsewhere",
    "other",
}


class DeleteAccountRequest(BaseModel):
    confirm: str
    reason: str | None = Field(default=None, max_length=40)
    details: str | None = Field(default=None, max_length=1000)


def _store_reason(user_id: str, reason: str | None, details: str | None) -> None:
    """Best effort: never block a deletion because feedback couldn't be saved."""
    reason = (reason or "").strip() or None
    details = (details or "").strip() or None
    if reason and reason not in DELETION_REASONS:
        reason = "other"
    if not reason and not details:
        return
    try:
        from db.supabase import get_supabase_admin

        client = get_supabase_admin()
        rows = client.table("users").select("user_role").eq("id", user_id).limit(1).execute().data or []
        client.table("account_deletion_reasons").insert(
            {
                "user_id": user_id,
                "user_role": rows[0].get("user_role") if rows else None,
                "reason": reason,
                "details": details,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).info("deletion reason not stored for %s: %s", user_id, exc)


@router.post("/me/delete")
async def delete_my_account(
    body: DeleteAccountRequest,
    response: Response,
    _: RateLimitOtp,
    user_id: str = Depends(get_current_user_id),
):
    """Permanently delete the signed-in account. Body must be {"confirm": "DELETE"}."""
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(
            status_code=400,
            detail={"detail": 'Type DELETE to confirm.', "code": "confirm_required"},
        )
    _store_reason(user_id, body.reason, body.details)
    try:
        summary = await delete_account(user_id, actor_id=user_id)
    except DeletionError as exc:
        status = 404 if exc.code == "not_found" else 409
        raise HTTPException(status_code=status, detail={"detail": str(exc), "code": exc.code}) from exc
    clear_auth_cookies(response)
    return {"message": "Your account has been deleted.", "summary": summary}
