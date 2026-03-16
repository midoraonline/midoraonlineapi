from typing import Literal

from pydantic import BaseModel

ShopType = Literal["product", "service", "both"]


class ShopCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    theme_config: dict | None = None
    shop_type: ShopType = "product"


class ShopUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    theme_config: dict | None = None
    shop_type: ShopType | None = None


class ShopResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    slug: str
    description: str | None
    logo_url: str | None
    theme_config: dict | None
    shop_type: ShopType
    is_active: bool
    subscription_end_date: str | None
    created_at: str | None
    updated_at: str | None


class ShopListItem(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    logo_url: str | None
    shop_type: ShopType
    is_active: bool
    created_at: str | None
