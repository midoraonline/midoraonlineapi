from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from auth.account_deletion import DeletionError, delete_account
from auth.cookies import clear_auth_cookies
from core.rate_limit import RateLimitOtp
from core.security import get_current_user_id

router = APIRouter()


class DeleteAccountRequest(BaseModel):
    confirm: str


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
    try:
        summary = await delete_account(user_id, actor_id=user_id)
    except DeletionError as exc:
        status = 404 if exc.code == "not_found" else 409
        raise HTTPException(status_code=status, detail={"detail": str(exc), "code": exc.code}) from exc
    clear_auth_cookies(response)
    return {"message": "Your account has been deleted.", "summary": summary}
