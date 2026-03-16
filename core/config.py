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
    nano_banana_api_key: str = ""
    nano_banana_url: str = ""

    # Admin API (optional): set ADMIN_API_KEY to protect admin routes
    admin_api_key: str = Field(default="", alias="ADMIN_API_KEY")


def get_settings() -> Settings:
    return Settings()
