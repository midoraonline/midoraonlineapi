from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Supabase (cloud): Dashboard → Project Settings → API → URL + Publishable key (+ Service role)
    supabase_url: str = Field(default="", validation_alias=AliasChoices("SUPABASE_URL", "supabase_url"))
    # Publishable key (public/client key; docs: was "anon key", now "publishable")
    supabase_anon_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY", "supabase_publishakey"),
    )
    supabase_service_role_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"),
    )

    # Email (SMTP) - .env uses Email, Email_Password
    email: str = Field(default="", alias="Email")
    email_password: str = Field(default="", alias="Email_Password")

    # Pesapal (optional for M1)
    pesapal_consumer_key: str = ""
    pesapal_consumer_secret: str = ""
    pesapal_ipn_url: str = ""

    # AI - .env uses Gemini_API_Key
    gemini_api_key: str = Field(default="", alias="Gemini_API_Key")
    # Override via GEMINI_MODEL env var; gemini-2.5-flash is confirmed working on free tier
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    nano_banana_api_key: str = ""
    nano_banana_url: str = ""

    # Admin API (optional): set ADMIN_API_KEY to protect admin routes
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")

    # App-auth JWT settings (for custom auth)
    app_jwt_secret: str = Field(default="CHANGE_ME_IN_PROD", alias="APP_JWT_SECRET")
    app_jwt_algorithm: str = Field(default="HS256", alias="APP_JWT_ALGORITHM")
    app_access_token_expire_minutes: int = Field(default=15, alias="APP_ACCESS_TOKEN_EXPIRE_MINUTES")
    app_refresh_token_expire_days: int = Field(default=30, alias="APP_REFRESH_TOKEN_EXPIRE_DAYS")

    # Google OAuth (Authorization Code flow)
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_SECRET")
    google_oauth_redirect_uri: str = Field(default="", alias="GOOGLE_OAUTH_REDIRECT_URI")
    google_oauth_frontend_callback_url: str = Field(
        default="",
        alias="GOOGLE_OAUTH_FRONTEND_CALLBACK_URL",
    )
    email_verification_frontend_url: str = Field(
        default="",
        alias="EMAIL_VERIFICATION_FRONTEND_URL",
    )

    # Public API base URL (for building links in emails)
    api_base_url: str = Field(default="http://localhost:8000", alias="API_BASE_URL")


def get_settings() -> Settings:
    return Settings()
