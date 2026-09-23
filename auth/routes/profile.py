from fastapi import APIRouter, Depends, HTTPException

from auth.schemas import ChangePasswordRequest, MessageResponse, ProfileResponse, UpdateProfileRequest
from core.security import get_current_user_id

router = APIRouter()


@router.patch("/me", response_model=ProfileResponse)
async def update_me(
    body: UpdateProfileRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import update_profile
    from auth.service import create_supabase_realtime_jwt

    try:
        update_avatar = "avatar_url" in body.model_fields_set
        profile = update_profile(
            user_id,
            body.full_name,
            body.phone_number,
            body.avatar_url,
            update_avatar=update_avatar,
        )
    except ValueError as e:
        msg = str(e)
        if "already linked" in msg.lower():
            raise HTTPException(status_code=409, detail={"detail": msg, "code": "phone_taken"})
        raise HTTPException(status_code=400, detail=msg)

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


@router.post("/change-password", response_model=MessageResponse)
async def change_password_endpoint(
    body: ChangePasswordRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import change_password

    try:
        change_password(user_id, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return MessageResponse(message="Password changed successfully")
