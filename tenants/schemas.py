from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.categories import validate_category_field

ShopType = Literal["product", "service", "both"]
UserRole = Literal["customer", "merchant", "admin", "staff"]


class ShopThemeConfig(BaseModel):
    """Stored as JSON in shops.theme_config. Extra keys are allowed for forward compatibility."""

    model_config = ConfigDict(extra="allow")

    primary_color: str | None = None
    background_color: str | None = None
    text_color: str | None = None
    accent_color: str | None = None
    font_family: str | None = None
    font: str | None = Field(default=None, description="Legacy alias; prefer font_family")
    theme: str | None = Field(default=None, description="Preset key, e.g. minimal, bold, boutique")
    metadata: dict[str, Any] | None = None


class ShopCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    about: str | None = None
    logo_url: str | None = None
    shop_email: str | None = None
    whatsapp_number: str | None = None
    contacts: list[dict] | None = None
    social_links: list[dict] | None = None
    location: dict | None = None
    availability: dict | None = None
    theme_config: ShopThemeConfig | dict[str, Any] | None = None
    shop_type: ShopType = "product"
    category: str | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        return validate_category_field(v)


class ShopUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    about: str | None = None
    logo_url: str | None = None
    shop_email: str | None = None
    whatsapp_number: str | None = None
    contacts: list[dict] | None = None
    social_links: list[dict] | None = None
    location: dict | None = None
    availability: dict | None = None
    theme_config: ShopThemeConfig | dict[str, Any] | None = None
    shop_type: ShopType | None = None
    category: str | None = None
    is_active: bool | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        return validate_category_field(v)


class ShopResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    slug: str
    category: str | None = None
    description: str | None
    about: str | None
    logo_url: str | None
    shop_email: str | None
    whatsapp_number: str | None
    contacts: list[dict] | None
    social_links: list[dict] | None
    location: dict | None
    availability: dict | None
    theme_config: dict[str, Any] | None
    shop_type: ShopType
    is_active: bool
    subscription_end_date: str | None
    created_at: str | None
    updated_at: str | None
    follower_count: int = 0
    like_count: int = 0
    view_count: int = 0
    trust_score: float = 0.0
    seller_score: float = 0.0
    fraud_score: float = 0.0
    available_now: bool = False
    last_seen_at: str | None = None
    viewer_following: bool | None = None
    viewer_liked_shop: bool | None = None


class ShopListItem(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    logo_url: str | None
    category: str | None = None
    location: dict[str, Any] | str | None = None
    shop_type: ShopType
    is_active: bool
    created_at: str | None
    view_count: int = 0
