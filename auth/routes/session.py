from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from auth.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from auth.schemas import ProfileResponse, RefreshRequest, TokenResponse
from auth.service import access_ttl_seconds, refresh_ttl_seconds, revoke_refresh_token
from core.security import get_current_user_id

router = APIRouter()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    cookie_refresh: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    from auth.providers.emailpassword import refresh_session

    token = (body.refresh_token if body else None) or cookie_refresh
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    try:
        result = refresh_session(
            token,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
    except Exception as e:
        # Any rotation failure invalidates the cookies for the browser.
        clear_auth_cookies(response)
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


@router.get("/me", response_model=ProfileResponse)
async def me(user_id: str = Depends(get_current_user_id)):
    from auth.providers.emailpassword import get_profile

    profile = get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileResponse(
        id=str(profile.get("id", "")),
        email=profile.get("email", ""),
        email_verified=bool(profile.get("email_verified")),
        full_name=profile.get("full_name"),
        avatar_url=profile.get("avatar_url"),
        phone_number=profile.get("phone_number"),
        user_role=profile.get("user_role", "customer"),
    )


@router.post("/logout")
async def logout(
    response: Response,
    cookie_refresh: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict:
    """Revoke the current refresh token (if present) and clear auth cookies."""
    if cookie_refresh:
        revoke_refresh_token(cookie_refresh)
    clear_auth_cookies(response)
    return {"message": "Logged out"}


@router.post("/upload-token", response_model=TokenResponse)
async def issue_upload_token(user_id: str = Depends(get_current_user_id)):
    """Mint a short-lived bearer token for third-party uploads (UploadThing).

    Cookies cannot be read from cross-origin Next.js server routes, so the
    browser calls this endpoint with credentials, then forwards the returned
    bearer on to uploadthing. The token inherits the user's current role.
    """
    from auth.providers.emailpassword import get_profile
    from auth.service import _build_access_claims, _encode_jwt

    profile = get_profile(user_id)
    role = (profile or {}).get("user_role", "customer") if profile else "customer"
    token = _encode_jwt(_build_access_claims(user_id, role))
    return TokenResponse(access_token=token, refresh_token="")
