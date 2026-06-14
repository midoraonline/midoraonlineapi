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
    # Address(es) that receive internal notifications (new shop submissions,
    # support pings). Comma-separated. Falls back to `email` (SMTP sender) when
    # unset, which effectively emails ourselves.
    admin_notification_email: str = Field(
        default="",
        validation_alias=AliasChoices("ADMIN_NOTIFICATION_EMAIL", "admin_notification_email"),
    )

    # Pesapal (optional for M1)
    pesapal_consumer_key: str = ""
    pesapal_consumer_secret: str = ""
    pesapal_ipn_url: str = ""
    # Base URL for Pesapal API. Sandbox vs. production.
    pesapal_api_base_url: str = Field(
        default="https://pay.pesapal.com/v3",
        alias="PESAPAL_API_BASE_URL",
    )
    # Subscription window (days) granted on a completed payment.
    subscription_duration_days: int = Field(
        default=30, alias="SUBSCRIPTION_DURATION_DAYS"
    )

    # AI - .env uses Gemini_API_Key
    gemini_api_key: str = Field(default="", alias="Gemini_API_Key")
    # Override via GEMINI_MODEL env var; gemini-2.5-flash is confirmed working on free tier
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001",
        alias="GEMINI_EMBEDDING_MODEL",
    )
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

    # Deployment environment: "development" | "production" | "test"
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Comma-separated list of allowed frontend origins for CORS
    # e.g. "http://localhost:3000,https://www.midoraonline.com"
    cors_allowed_origins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")

    # Next.js storefront: used to POST /api/revalidate after shop/product mutations
    # so Vercel Data Cache (unstable_cache) reflects changes immediately.
    frontend_public_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "FRONTEND_PUBLIC_URL",
            "NEXT_PUBLIC_SITE_URL",
            "NEXT_PUBLIC_URL",
        ),
    )
    revalidate_secret: str = Field(
        default="",
        validation_alias=AliasChoices("REVALIDATE_SECRET", "NEXT_REVALIDATE_SECRET"),
    )

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def admin_notification_recipients(self) -> list[str]:
        raw = self.admin_notification_email.strip()
        if not raw:
            # Fallback: send to the SMTP sender address (i.e. email ourselves).
            return [self.email] if self.email else []
        return [e.strip() for e in raw.split(",") if e.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_allowed_origins.strip()
        if not raw:
            # Dev default: allow the common Next.js local origins
            return [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ]
        return [o.strip() for o in raw.split(",") if o.strip()]


def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production:
        missing: list[str] = []
        if not settings.app_jwt_secret or settings.app_jwt_secret == "CHANGE_ME_IN_PROD":
            missing.append("APP_JWT_SECRET")
        if not settings.supabase_url:
            missing.append("SUPABASE_URL")
        if not settings.supabase_service_role_key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        if not settings.admin_api_key:
            missing.append("ADMIN_API_KEY")
        if not settings.cors_allowed_origins.strip():
            missing.append("CORS_ALLOWED_ORIGINS")
        if missing:
            raise RuntimeError(
                "Missing required production environment variables: " + ", ".join(missing)
            )
    return settings
