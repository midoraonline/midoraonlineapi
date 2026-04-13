from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

from auth.providers.googleauth import generate_state_token, get_redirect_url, handle_callback
from auth.schemas import GoogleCodeExchangeRequest, GoogleOAuthUrlResponse
from core.config import get_settings

router = APIRouter()


@router.get("/google/url", response_model=GoogleOAuthUrlResponse)
async def google_oauth_url(state: str | None = Query(default=None)):
    try:
        safe_state = state or generate_state_token()
        return GoogleOAuthUrlResponse(
            url=get_redirect_url(state=safe_state),
            state=safe_state,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/google/callback")
async def google_oauth_callback(code: str = Query(...), state: str | None = Query(default=None)):
    settings = get_settings()
    frontend_callback_url = settings.google_oauth_frontend_callback_url.strip()
    try:
        result = handle_callback(code=code, state=state)
        if frontend_callback_url:
            fragment = urlencode(
                {
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "token_type": "bearer",
                    "provider": "google",
                }
            )
            return RedirectResponse(url=f"{frontend_callback_url}#{fragment}", status_code=302)
        return {
            "message": "Google sign-in successful",
            "user": result["user"],
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": "bearer",
        }
    except Exception as e:
        if frontend_callback_url:
            fragment = urlencode({"error": str(e), "provider": "google"})
            return RedirectResponse(url=f"{frontend_callback_url}#{fragment}", status_code=302)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/google/exchange")
async def google_oauth_exchange(body: GoogleCodeExchangeRequest):
    try:
        result = handle_callback(code=body.code, state=body.state)
        return {
            "user": result["user"],
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": "bearer",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
