from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.schemas import PaginationParams
from admin import service as admin_service

router = APIRouter()


@router.get("/")
async def list_all_shops(params: Annotated[PaginationParams, Depends()]):
    return admin_service.list_all_shops(page=params.page, limit=params.limit)


@router.patch("/{shop_id}/active")
async def set_shop_active(shop_id: str, is_active: bool = True):
    result = admin_service.set_shop_active(shop_id, is_active)
    if not result:
        raise HTTPException(status_code=404, detail="Shop not found")
    return result
