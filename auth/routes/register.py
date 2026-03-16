from fastapi import APIRouter, HTTPException

from auth.providers.emailpassword import sign_up
from auth.schemas import RegisterRequest, TokenResponse

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest):
    try:
        result = sign_up(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            user_role=body.user_role,
        )
        session = result.get("session")
        if session:
            return TokenResponse(
                access_token=getattr(session, "access_token", "") or "",
                refresh_token=getattr(session, "refresh_token", None) or "",
            )
        # Email confirmation required - no session yet
        return TokenResponse(access_token="", refresh_token="")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
