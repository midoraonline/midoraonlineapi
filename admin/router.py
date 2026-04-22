from fastapi import APIRouter, Depends

from core.security import require_admin
from admin.routes import shops as admin_shops
from admin.routes import stats as admin_stats
from admin.routes import subscriptions as admin_subs
from tenants.routes import verifications as verif_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
router.include_router(admin_shops.router, prefix="/shops", tags=["admin"])
router.include_router(admin_subs.router, prefix="/subscriptions", tags=["admin"])
router.include_router(admin_stats.router, prefix="/stats", tags=["admin"])
router.include_router(verif_admin.admin_router, prefix="/shops", tags=["admin"])
