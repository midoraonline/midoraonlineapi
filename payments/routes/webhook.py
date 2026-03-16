from fastapi import APIRouter, Request

from payments import service as payments_service

router = APIRouter()


@router.post("/webhook")
async def pesapal_webhook(request: Request):
    """Public IPN endpoint. Verify signature, idempotency, update subscriptions/shops with service_role."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    payments_service.process_webhook(body)
    return {"received": True}
