from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

from auth.providers import verify_email as do_verify
from core.config import get_settings

router = APIRouter()


@router.api_route("/verify-email", methods=["GET", "POST"])
async def verify_email_endpoint(token: str = Query(...)):
    settings = get_settings()
    frontend_verify_url = settings.email_verification_frontend_url.strip()
    try:
        result = do_verify(token)
        if frontend_verify_url:
            fragment = urlencode(
                {
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "token_type": "bearer",
                    "verified": "true",
                }
            )
            return RedirectResponse(url=f"{frontend_verify_url}#{fragment}", status_code=302)
        # Return user details and fresh tokens
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
