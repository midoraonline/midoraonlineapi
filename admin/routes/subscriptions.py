from fastapi import APIRouter

from admin import service as admin_service

router = APIRouter()


@router.get("/")
async def list_all_subscriptions():
    return admin_service.list_all_subscriptions()
