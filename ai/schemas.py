from typing import Literal

from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    """shop_id required for in-shop chat; omit when intent='create_shop'."""
    shop_id: str | None = None
    customer_id: str | None = None
    intent: Literal["create_shop"] | None = None


class SuggestedShop(BaseModel):
    """AI-suggested shop payload for create-shop flow. Client can POST /shops with this."""
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    shop_type: Literal["product", "service", "both"] = "product"


class MessageCreate(BaseModel):
    message: str


class MessageResponse(BaseModel):
    id: str
    message: str
    sender_type: str
    created_at: str | None


class SendMessageResponse(BaseModel):
    """Response from POST .../messages. suggested_shop set when intent=create_shop and AI has enough info."""
    message: str
    sender_type: str = "ai_concierge"
    suggested_shop: SuggestedShop | None = None


class AIContextCreate(BaseModel):
    context_type: str
    content: str


class AIContextResponse(BaseModel):
    id: str
    shop_id: str
    context_type: str
    content: str
    last_updated: str | None


class RemoveBackgroundRequest(BaseModel):
    image_url: str | None = None
    image_base64: str | None = None


class RemoveBackgroundResponse(BaseModel):
    image_url: str | None = None
    image_base64: str | None = None


class GenerateLogoRequest(BaseModel):
    prompt: str | None = None
    style: str | None = None


class GenerateLogoResponse(BaseModel):
    logo_url: str | None = None
    logo_base64: str | None = None
