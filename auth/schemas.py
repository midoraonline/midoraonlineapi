from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    user_role: str = "customer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class GoogleCodeExchangeRequest(BaseModel):
    code: str
    state: str | None = None


class GoogleOAuthUrlResponse(BaseModel):
    url: str
    state: str


class ProfileResponse(BaseModel):
    id: str
    full_name: str | None
    avatar_url: str | None
    phone_number: str | None
    user_role: str
