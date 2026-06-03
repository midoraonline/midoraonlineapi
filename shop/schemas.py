from pydantic import BaseModel, field_validator

from core.categories import validate_category_field


class ProductCreate(BaseModel):
    title: str
    description: str | None = None
    price_ugx: float
    stock_quantity: int = 0
    image_urls: list[str] | str | None = None
    category: str | None = None
    is_published: bool = True
    item_type: str | None = None
    location_name: str | None = None

    @field_validator("image_urls", mode="before")
    @classmethod
    def _coerce_image_urls(cls, v: list[str] | str | None) -> list[str] | None:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        return validate_category_field(v)


class ProductUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    price_ugx: float | None = None
    stock_quantity: int | None = None
    image_urls: list[str] | str | None = None

    @field_validator("image_urls", mode="before")
    @classmethod
    def _coerce_image_urls(cls, v: list[str] | str | None) -> list[str] | None:
        if v is None:
            return None
        if v == "":
            return []
        if isinstance(v, str):
            return [v]
        return v
    category: str | None = None
    is_published: bool | None = None
    item_type: str | None = None
    status: str | None = None
    location_name: str | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        return validate_category_field(v)


class ProductResponse(BaseModel):
    id: str
    shop_id: str
    title: str
    description: str | None
    price_ugx: float
    stock_quantity: int
    image_urls: list[str] | None
    category: str | None
    is_published: bool
    item_type: str | None = None
    status: str | None = None
    listing_score: int = 0
    location_name: str | None = None
    created_at: str | None
    like_count: int = 0
    view_count: int = 0
    viewer_liked: bool | None = None


class ProductListItem(BaseModel):
    id: str
    shop_id: str
    title: str
    description: str | None = None
    price_ugx: float
    image_urls: list[str] | None = None
    category: str | None
    is_published: bool
    item_type: str | None = None
    status: str | None = None
    listing_score: int = 0
    location_name: str | None = None
    created_at: str | None
    view_count: int = 0


class OrderCreate(BaseModel):
    shop_id: str
    total_amount: float


class OrderUpdate(BaseModel):
    order_status: str | None = None


class OrderResponse(BaseModel):
    id: str
    customer_id: str
    shop_id: str
    total_amount: float
    order_status: str
    created_at: str | None


class OrderListItem(BaseModel):
    id: str
    shop_id: str
    total_amount: float
    order_status: str
    created_at: str | None


class ShopEngagementState(BaseModel):
    follower_count: int
    like_count: int
    view_count: int = 0
    viewer_following: bool | None = None
    viewer_liked_shop: bool | None = None
    whatsapp_clicks: int = 0
    messages: int = 0


class ProductEngagementState(BaseModel):
    like_count: int
    view_count: int = 0
    viewer_liked: bool | None = None
    whatsapp_clicks: int = 0
    messages: int = 0


class ViewCountResponse(BaseModel):
    view_count: int
