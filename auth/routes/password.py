import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from auth.password_reset import ResetError, create_reset_token, reset_password
from auth.schemas import MessageResponse, _MAX_PASSWORD_LEN, _MIN_PASSWORD_LEN
from core.config import get_settings
from core.rate_limit import RateLimitOtp

logger = logging.getLogger(__name__)
router = APIRouter()

SENT = "If an account uses that email, we've sent a link to reset the password."


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=_MIN_PASSWORD_LEN, max_length=_MAX_PASSWORD_LEN)


@router.post("/password/forgot", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest, _: RateLimitOtp) -> MessageResponse:
    from mail.send import send_password_reset_email

    try:
        created = create_reset_token(str(body.email))
    except Exception:
        logger.exception("create_reset_token failed")
        raise HTTPException(
            status_code=503,
            detail={"detail": "Password reset is unavailable right now. Try again soon.", "code": "reset_unavailable"},
        )
    if created:
        token, user = created
        link = f"{get_settings().frontend_url}/auth/reset-password?token={token}"
        try:
            await send_password_reset_email(str(user["email"]), link)
        except Exception as exc:
            logger.warning("password reset email failed: %s", exc)
    return MessageResponse(message=SENT)


@router.post("/password/reset", response_model=MessageResponse)
async def reset_password_endpoint(body: ResetPasswordRequest, _: RateLimitOtp) -> MessageResponse:
    try:
        reset_password(body.token, body.password)
    except ResetError as exc:
        raise HTTPException(status_code=400, detail={"detail": str(exc), "code": exc.code}) from exc
    return MessageResponse(message="Password updated. Sign in with your new password.")
