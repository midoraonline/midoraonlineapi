from fastapi import APIRouter

from payments.routes import subscriptions, webhook

router = APIRouter(prefix="/payments", tags=["payments"])
router.include_router(subscriptions.router)
router.include_router(webhook.router)
