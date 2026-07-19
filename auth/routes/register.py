import logging

from fastapi import APIRouter, HTTPException, Request, Response

from auth.cookies import set_auth_cookies
from auth.providers.emailpassword import sign_up
from auth.schemas import RegisterRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds
from core.config import get_settings
from mail.send import send_verification_email

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
):
    try:
        result = sign_up(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            user_role=body.user_role,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
