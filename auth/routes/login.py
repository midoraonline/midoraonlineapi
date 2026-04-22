from fastapi import APIRouter, HTTPException, Request, Response

from auth.cookies import set_auth_cookies
from auth.providers.emailpassword import sign_in
from auth.schemas import LoginRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response):
    try:
        result = sign_in(
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

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
