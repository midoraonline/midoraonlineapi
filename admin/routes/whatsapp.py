import asyncio

from fastapi import APIRouter

from notifications import whatsapp_cloud

router = APIRouter()


@router.get("/whatsapp/status")
async def admin_whatsapp_status() -> dict:
    """Admin: is the WhatsApp Cloud API ready to send verification codes, and if not, why."""
    return await asyncio.to_thread(whatsapp_cloud.connection_status)
