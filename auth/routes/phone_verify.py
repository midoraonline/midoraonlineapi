from fastapi import APIRouter, Depends, HTTPException

from auth.schemas import ConfirmCodeRequest, MessageResponse, ProfileResponse, SendPhoneCodeRequest
from core.security import get_current_user_id
from core.rate_limit import RateLimitOtp

router = APIRouter()


@router.post("/phone/send-code", response_model=MessageResponse)
async def send_phone_code(
    body: SendPhoneCodeRequest,
    _: RateLimitOtp,
    user_id: str = Depends(get_current_user_id),
):
    from common.verification_service import send_verification_code

    try:
        send_verification_code(
            user_id=user_id,
            phone_number=body.phone_number,
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return MessageResponse(message="Verification code sent")


@router.post("/phone/verify", response_model=ProfileResponse)
async def verify_phone_code(
    body: ConfirmCodeRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import get_profile
    from auth.service import create_supabase_realtime_jwt
    from common.verification_service import confirm_verification_code

    try:
        phone_number = confirm_verification_code(
            user_id=user_id,
            code=body.code,
            purpose="phone",
            target_type="user",
            target_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from db.supabase import get_supabase_admin

    get_supabase_admin().table("users").update(
        {"phone_number": phone_number, "phone_verified": True}
    ).eq("id", user_id).execute()
    try:
        get_supabase_admin().table("profiles").update({"phone_number": phone_number}).eq("id", user_id).execute()
    except Exception:
        pass  # profiles row may not exist for every user

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
        phone_verified=bool(profile.get("phone_verified")),
        user_role=profile.get("user_role", "customer"),
        supabase_realtime_token=create_supabase_realtime_jwt(user_id),
    )
