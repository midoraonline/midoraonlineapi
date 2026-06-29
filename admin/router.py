from fastapi import APIRouter, Depends

from core.security import require_admin
from admin.routes import shops as admin_shops
from admin.routes import stats as admin_stats
from admin.routes import subscriptions as admin_subs
from admin.routes import listings as admin_listings
from admin.routes import sellers as admin_sellers
from admin.routes import boosts as admin_boosts
from admin.routes import fraud as admin_fraud
from admin.routes import notifications_broadcast as admin_notifications
from admin.routes import settings as admin_settings
from admin.routes import reports as admin_reports
from admin.routes import comments_admin as admin_comments
from admin.routes import chat_admin as admin_chat
from admin.routes import feedback as admin_feedback
from tenants.routes import verifications as verif_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
router.include_router(admin_shops.router, prefix="/shops", tags=["admin"])
router.include_router(admin_subs.router, prefix="/subscriptions", tags=["admin"])
router.include_router(admin_stats.router, prefix="/stats", tags=["admin"])
router.include_router(admin_listings.router, prefix="", tags=["admin"])
router.include_router(admin_sellers.router, prefix="", tags=["admin"])
router.include_router(admin_boosts.router, prefix="", tags=["admin"])
router.include_router(admin_fraud.router, prefix="", tags=["admin"])
router.include_router(admin_notifications.router, prefix="", tags=["admin"])
router.include_router(admin_settings.router, prefix="", tags=["admin"])
router.include_router(admin_reports.router, prefix="", tags=["admin"])
router.include_router(admin_comments.router, prefix="", tags=["admin"])
router.include_router(admin_chat.router, prefix="", tags=["admin"])
router.include_router(admin_feedback.router, prefix="", tags=["admin"])
router.include_router(verif_admin.admin_router, prefix="/shops", tags=["admin"])
