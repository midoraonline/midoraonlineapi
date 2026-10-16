from pydantic import BaseModel


class SubscribeRequest(BaseModel):
    shop_id: str
    plan_tier: str = "standard"


class SubscriptionResponse(BaseModel):
    id: str
    shop_id: str
    merchant_reference: str
    payment_status: str
    amount: float
    plan_tier: str | None = None
    created_at: str | None


class PlanResponse(BaseModel):
    key: str
    name: str
    price_ugx: float
    currency: str
    billing_period_days: int
    max_shops: int
    max_products_per_shop: int
    analytics_enabled: bool
    frequency_per_day: int = 0
    frequency_per_day_boosted: int = 0
    max_photos: int | None = None
    max_videos: int | None = None
    free_boosts_per_month: int = 0
    reposts_per_day: int = 0
    repost_cooldown_hours: int = 0
    features: list[str] = []
