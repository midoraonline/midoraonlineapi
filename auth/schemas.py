from typing import Literal

from pydantic import BaseModel, EmailStr

UserRole = Literal["customer", "merchant", "admin", "staff"]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    user_role: UserRole = "customer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
    user_role: str
    plan_tier: str = "basic"
    plan_expires_at: str | None = None
    # Short-lived JWT for Supabase Realtime subscriptions. Signed with the
    # app JWT secret and carries `role: "authenticated"` so Supabase RLS
    # runs as the current user (see `create_supabase_realtime_jwt`).
    supabase_realtime_token: str | None = None


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MessageResponse(BaseModel):
    message: str


class SendPhoneCodeRequest(BaseModel):
    phone_number: str


class ConfirmCodeRequest(BaseModel):
    code: str

