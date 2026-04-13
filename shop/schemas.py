from pydantic import BaseModel


class ProductCreate(BaseModel):
    title: str
    description: str | None = None
    price_ugx: float
    stock_quantity: int = 0
    image_urls: list[str] | None = None
    category: str | None = None
    is_published: bool = True


class ProductUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    price_ugx: float | None = None
    stock_quantity: int | None = None
    image_urls: list[str] | None = None
    category: str | None = None
    is_published: bool | None = None


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


class ProductEngagementState(BaseModel):
    like_count: int
    view_count: int = 0
    viewer_liked: bool | None = None


class ViewCountResponse(BaseModel):
    view_count: int
