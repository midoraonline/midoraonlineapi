import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth.cookies import set_auth_cookies
from auth.providers.emailpassword import sign_up
from auth.schemas import RegisterRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds
from core.config import get_settings
from core.rate_limit import RateLimitOtp, RateLimitRegister
from core.security import get_current_user_id
from mail.send import send_verification_email

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    _: RateLimitRegister,
):
    from platform_settings.flags import SignupsClosed, assert_signups_open

    try:
        assert_signups_open()
        result = sign_up(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            user_role=body.user_role,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except SignupsClosed as exc:
        raise HTTPException(
            status_code=403,
            detail={"detail": str(exc), "code": "signups_closed"},
        ) from exc
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("register failed")
        raise HTTPException(status_code=400, detail="Registration failed")

    settings = get_settings()
    base_url = settings.api_base_url.rstrip("/")
    verification_link = (
        f"{base_url}/api/v1/auth/verify-email?token={result['verification_token']}"
    )

    # Send inline (best-effort). On serverless (Vercel) BackgroundTasks that
    # run after the response can be killed mid-execution, causing silently
    # dropped verification emails. Sending inline is a few hundred ms more
    # for the caller but guarantees delivery is attempted.
    try:
        await send_verification_email(body.email, verification_link)
    except Exception as exc:
        logger.warning("send_verification_email failed for %s: %s", body.email, exc)

    set_auth_cookies(
        response,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        access_ttl_seconds=access_ttl_seconds(),
        refresh_ttl_seconds=refresh_ttl_seconds(),
    )
    return TokenResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
    )


@router.post("/verify-email/resend")
async def resend_verification_email(
    _: RateLimitOtp,
    user_id: str = Depends(get_current_user_id),
):
    """Send a fresh verification link to the signed-in user's email."""
    from uuid import uuid4

    from db.supabase import get_supabase_admin

    client = get_supabase_admin()
    rows = (
        client.table("users")
        .select("email, email_verified")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows or not rows[0].get("email"):
        raise HTTPException(status_code=404, detail="Account not found")
    if rows[0].get("email_verified"):
        return {"message": "Email already verified", "already_verified": True}
    token = uuid4().hex
    client.table("email_verification_tokens").insert(
        {"user_id": user_id, "token": token}
    ).execute()
    link = f"{get_settings().api_base_url.rstrip('/')}/api/v1/auth/verify-email?token={token}"
    try:
        await send_verification_email(rows[0]["email"], link)
    except Exception as exc:
        logger.warning("resend verification failed for %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"detail": "We couldn't send the email right now.", "code": "email_unavailable"},
        ) from exc
    return {"message": "Verification email sent", "already_verified": False}
