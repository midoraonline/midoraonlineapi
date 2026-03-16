from pydantic import BaseModel


class SubscribeRequest(BaseModel):
    shop_id: str
    amount: float = 5000.0
    currency: str = "UGX"


class SubscriptionResponse(BaseModel):
    id: str
    shop_id: str
    merchant_reference: str
    payment_status: str
    amount: float
    created_at: str | None
