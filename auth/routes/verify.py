from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

from auth.cookies import set_auth_cookies
from auth.providers import verify_email as do_verify
from auth.service import access_ttl_seconds, refresh_ttl_seconds
from core.config import get_settings

router = APIRouter()


@router.api_route("/verify-email", methods=["GET", "POST"])
async def verify_email_endpoint(request: Request, token: str = Query(...)):
    settings = get_settings()
    frontend_verify_url = settings.email_verification_frontend_url.strip()
    try:
        result = do_verify(
            token,
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
        if frontend_verify_url:
            fragment = urlencode(
                {
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "token_type": "bearer",
                    "verified": "true",
                }
            )
            response = RedirectResponse(
                url=f"{frontend_verify_url}#{fragment}", status_code=302
            )
            set_auth_cookies(
                response,
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                access_ttl_seconds=access_ttl_seconds(),
                refresh_ttl_seconds=refresh_ttl_seconds(),
            )
            return response
        return {
            "message": "Email verified",
            "user": result["user"],
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": "bearer",
        }
    except Exception as e:
        if frontend_verify_url:
            fragment = urlencode({"error": str(e), "verified": "false"})
            return RedirectResponse(url=f"{frontend_verify_url}#{fragment}", status_code=302)
        raise HTTPException(status_code=400, detail=str(e))
