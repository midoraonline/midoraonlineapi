from fastapi import APIRouter, HTTPException

from auth.providers.emailpassword import sign_in
from auth.schemas import LoginRequest, TokenResponse

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    try:
        result = sign_in(email=body.email, password=body.password)
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
