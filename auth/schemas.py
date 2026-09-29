from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from auth.preferences import UserPreferences, UserPreferencesPatch

UserRole = Literal["customer", "merchant", "admin", "staff"]
PublicUserRole = Literal["customer", "merchant"]

_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=_MIN_PASSWORD_LEN, max_length=_MAX_PASSWORD_LEN)
    full_name: str | None = None
    user_role: PublicUserRole = "customer"

    @field_validator("user_role", mode="before")
    @classmethod
    def _public_role_only(cls, v: object) -> str:
        role = str(v or "customer").strip().lower()
        if role in ("admin", "staff"):
            return "customer"
        if role not in ("customer", "merchant"):
            return "customer"
        return role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LEN)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Optional JSON body for /auth/refresh.

    The refresh token is normally delivered via the `midora_refresh` cookie.
    This body shape is kept for non-browser clients (mobile apps, scripts).
    """

    refresh_token: str | None = None


class GoogleCodeExchangeRequest(BaseModel):
    code: str
    state: str | None = None


class GoogleOAuthUrlResponse(BaseModel):
    url: str
    state: str


class ProfileResponse(BaseModel):
    id: str
    email: EmailStr
    email_verified: bool
    full_name: str | None
    avatar_url: str | None
    phone_number: str | None
    phone_verified: bool = False
    bio: str | None = None
    user_role: str
    plan_tier: str = "basic"
    plan_expires_at: str | None = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    # Short-lived JWT for Supabase Realtime subscriptions. Signed with the
    # app JWT secret and carries `role: "authenticated"` so Supabase RLS
    # runs as the current user (see `create_supabase_realtime_jwt`).
    supabase_realtime_token: str | None = None


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None
    # Empty string clears the avatar; omit to leave unchanged.
    avatar_url: str | None = None
    # Empty string clears the bio; omit to leave unchanged. Max 500 characters.
    bio: str | None = None
    preferences: UserPreferencesPatch | None = None


def profile_response(profile: dict[str, Any], *, realtime_token: str | None) -> ProfileResponse:
    from auth.preferences import coerce_preferences

    prefs = coerce_preferences(profile.get("preferences"))
    return ProfileResponse(
        id=str(profile.get("id", "")),
        email=profile.get("email", ""),
        email_verified=bool(profile.get("email_verified")),
        full_name=profile.get("full_name"),
        avatar_url=profile.get("avatar_url"),
        phone_number=profile.get("phone_number"),
        phone_verified=bool(profile.get("phone_verified")),
        bio=profile.get("bio"),
        user_role=profile.get("user_role", "customer"),
        plan_tier=profile.get("plan_tier") or "basic",
        plan_expires_at=str(profile["plan_expires_at"]) if profile.get("plan_expires_at") else None,
        preferences=UserPreferences.model_validate(prefs),
        supabase_realtime_token=realtime_token,
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=_MAX_PASSWORD_LEN)
    new_password: str = Field(min_length=_MIN_PASSWORD_LEN, max_length=_MAX_PASSWORD_LEN)


class MessageResponse(BaseModel):
    message: str


class SendPhoneCodeRequest(BaseModel):
    phone_number: str


class ConfirmCodeRequest(BaseModel):
    code: str


class VerificationStatusResponse(BaseModel):
    phone: str | None = None
    phone_verified: bool = False
    email: str | None = None
    email_verified: bool = False
    can_post: bool = False
    required_channel: Literal["phone", "email"] | None = None


class PhoneOtpStartRequest(BaseModel):
    phone: str


class PhoneOtpConfirmRequest(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=8)


class EmailOtpStartRequest(BaseModel):
    email: EmailStr


class EmailOtpConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)

