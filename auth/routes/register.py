from fastapi import APIRouter, BackgroundTasks, HTTPException

from auth.providers.emailpassword import sign_up
from auth.schemas import RegisterRequest, TokenResponse
from core.config import get_settings
from mail.send import send_verification_email

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, background_tasks: BackgroundTasks):
    try:
        result = sign_up(
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            user_role=body.user_role,
        )
        # Send verification email asynchronously
        settings = get_settings()
        base_url = settings.api_base_url.rstrip("/")
        verification_link = f"{base_url}/api/v1/auth/verify-email?token={result['verification_token']}"
        background_tasks.add_task(send_verification_email, body.email, verification_link)

        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
