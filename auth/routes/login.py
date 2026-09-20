import logging

from fastapi import APIRouter, HTTPException, Request, Response

from auth.cookies import set_auth_cookies
from auth.providers.emailpassword import sign_in
from auth.schemas import LoginRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds
from core.rate_limit import RateLimitLogin, check_rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    _: RateLimitLogin,
):
    check_rate_limit(
        f"auth.login:email:{body.email.strip().lower()}",
        limit=5,
        window_seconds=60,
    )
    try:
        result = sign_in(
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    except Exception:
        logger.exception("login failed")
        raise HTTPException(status_code=401, detail="Invalid email or password")

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
