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


class ProductListItem(BaseModel):
    id: str
    shop_id: str
    title: str
    price_ugx: float
    category: str | None
    is_published: bool
    created_at: str | None


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
