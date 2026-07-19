from pydantic import BaseModel, field_validator

from core.categories import validate_category_field


class ProductCreate(BaseModel):
    title: str
    description: str | None = None
    price_ugx: float
    discount_price: float | None = None
    discount_expires_at: str | None = None
    stock_quantity: int = 0
    image_urls: list[str] | str | None = None
    category: str | None = None
    is_published: bool = True
    is_negotiable: bool = True
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
    discount_price: float | None = None
    discount_expires_at: str | None = None
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
    is_negotiable: bool | None = None
    item_type: str | None = None
    status: str | None = None
    location_name: str | None = None
    ai_seo_tags: str | None = None

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
    discount_price: float | None = None
    discount_expires_at: str | None = None
    stock_quantity: int
    image_urls: list[str] | None
    category: str | None
    is_published: bool
    is_negotiable: bool = True
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
    discount_price: float | None = None
    discount_expires_at: str | None = None
    image_urls: list[str] | None = None
    category: str | None
    is_published: bool
    is_negotiable: bool = True
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


class ShopSummary(BaseModel):
    """Lightweight shop snapshot embedded in product detail responses."""

    id: str
    name: str
    slug: str | None = None
    logo_url: str | None = None
    whatsapp_number: str | None = None
    is_active: bool = True
    trust_score: int = 0
    trust_badges: list[str] = []
    available_now: bool = False
    location: str | None = None


class ProductDetailResponse(BaseModel):
    """Rich product detail — product + shop + engagement in a single response.

    Replaces the old pattern of:
      GET /products/{id}           -> product row
      GET /products/{id}/engagement -> engagement (like_count, viewer_liked, …)
    with a single O(3-query) composite fetch, eliminating 4+ sequential
    round-trips and the separate frontend shop lookup.
    """

    # Core product fields
    id: str
    shop_id: str
    title: str
    description: str | None
    price_ugx: float
    discount_price: float | None = None
    discount_expires_at: str | None = None
    stock_quantity: int
    image_urls: list[str]
    category: str | None
    item_type: str | None = None
    status: str | None = None
    is_published: bool
    listing_score: int = 0
    location_name: str | None = None
    ai_seo_tags: str | None = None
    ai_generated_desc: bool = False
    created_at: str | None

    # Engagement — fetched in a single batched pass
    like_count: int = 0
    view_count: int = 0
    viewer_liked: bool | None = None
    whatsapp_clicks: int = 0
    messages: int = 0

    # Boost status — resolved in the same call
    boosted: bool = False

    # Embedded shop snapshot — eliminates a separate shop fetch on the frontend
    shop: ShopSummary | None = None


class DiscountSet(BaseModel):
    discount_price: float | None = None
    discount_expires_at: str | None = None


class ProductCard(BaseModel):
    """Denormalised product card for carousels and grid lists.

    Superset schema so a single serializer can back liked-products, trending,
    premium, and any future card feed without divergent shapes on the client.
    Every consumer-specific field is optional so tighter mappers can omit them.
    """

    id: str
    shop_id: str
    title: str
    description: str | None = None
    price_ugx: float
    discount_price: float | None = None
    discount_expires_at: str | None = None
    image_urls: list[str] | None = None
    category: str | None = None
    item_type: str = "product"
    status: str = "active"
    listing_score: int = 0
    location_name: str | None = None
    is_published: bool = True
    is_negotiable: bool = True
    view_count: int = 0
    created_at: str | None = None

    # Reviews (optional — omitted by carousels)
    average_rating: float = 0.0
    review_count: int = 0

    # Embedded shop snapshot
    shop_name: str | None = None
    shop_slug: str | None = None
    shop_whatsapp: str | None = None
    owner_id: str | None = None
    shop_is_active: bool = True
    shop_trust_badges: list[str] = []
    shop_available_now: bool = False


class PaginatedProductCards(BaseModel):
    items: list[ProductCard]
    total: int
    page: int
    limit: int
