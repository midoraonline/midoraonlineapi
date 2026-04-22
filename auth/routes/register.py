from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from auth.cookies import set_auth_cookies
from auth.providers.emailpassword import sign_up
from auth.schemas import RegisterRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds
from core.config import get_settings
from mail.send import send_verification_email

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(send_verification_email, body.email, verification_link)

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
