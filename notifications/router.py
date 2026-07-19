from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.security import get_current_user_id
from notifications.push_service import (
    delete_subscription_by_endpoint,
    save_subscription,
    send_to_user,
    vapid_public_key,
)

router = APIRouter()


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1)
    auth: str = Field(..., min_length=1)


class PushSubscriptionBody(BaseModel):
    """Shape returned by the browser's `PushManager.subscribe()`."""

    endpoint: str = Field(..., min_length=1)
    keys: PushKeys


class UnsubscribeBody(BaseModel):
    endpoint: str = Field(..., min_length=1)


@router.get("/public-key")
async def get_vapid_public_key() -> dict[str, str]:
    """Return the VAPID public key so the browser can subscribe.

    Exposed anonymously — the public key is safe to serve to unauthenticated
    clients (that's the whole point of "public").
    """
    return {"public_key": vapid_public_key()}


@router.post("/subscribe")
async def subscribe(
    body: PushSubscriptionBody,
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict[str, Any]:
    saved = save_subscription(
        user_id=user_id,
        subscription=body.model_dump(),
        user_agent=request.headers.get("user-agent"),
    )
    if not saved:
        raise HTTPException(status_code=502, detail="Failed to save subscription")
    return {"status": "ok", "id": saved.get("id")}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeBody,
    _user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict[str, str]:
    delete_subscription_by_endpoint(body.endpoint)
    return {"status": "ok"}


@router.post("/test")
async def send_test_push(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> dict[str, Any]:
    """Fire a test push to the current user's registered devices."""
    count = send_to_user(
        user_id,
        {
            "title": "Midora",
            "body": "Push notifications are working \u2728",
            "url": "/chat",
        },
    )
    return {"delivered": count}
