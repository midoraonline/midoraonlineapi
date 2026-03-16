from fastapi import APIRouter, Depends

from core.security import require_admin
from admin.routes import shops as admin_shops
from admin.routes import subscriptions as admin_subs

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
router.include_router(admin_shops.router, prefix="/shops", tags=["admin"])
router.include_router(admin_subs.router, prefix="/subscriptions", tags=["admin"])
