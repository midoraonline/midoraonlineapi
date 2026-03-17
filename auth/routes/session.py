from fastapi import APIRouter, Depends, HTTPException

from auth.schemas import ProfileResponse, RefreshRequest, TokenResponse
from core.security import get_current_user_id

router = APIRouter()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    from auth.providers.emailpassword import refresh_session

    try:
        result = refresh_session(body.refresh_token)
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/me", response_model=ProfileResponse)
async def me(user_id: str = Depends(get_current_user_id)):
    from auth.providers.emailpassword import get_profile

    profile = get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileResponse(
        id=str(profile.get("id", "")),
        full_name=profile.get("full_name"),
        avatar_url=profile.get("avatar_url"),
        phone_number=profile.get("phone_number"),
        user_role=profile.get("user_role", "customer"),
    )


@router.post("/logout")
async def logout() -> dict:
    """Stateless logout: client should delete stored tokens."""
    return {"message": "Logged out. Please delete tokens on client."}
