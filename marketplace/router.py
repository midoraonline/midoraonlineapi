from fastapi import APIRouter

from marketplace.routes import boost_plans as bp
from marketplace.routes import listing_events as le
from marketplace.routes import leads as ld
from marketplace.routes import reviews as rv
from marketplace.routes import notifications as nt
from marketplace.routes import comments as cm
from marketplace.routes import reports as rp
from marketplace.routes import chat_native as cn

router = APIRouter(tags=["marketplace"])

router.include_router(bp.router, prefix="/boost-plans", tags=["marketplace"])
router.include_router(le.router, prefix="/products", tags=["marketplace"])
router.include_router(ld.router, prefix="/shops", tags=["marketplace"])
router.include_router(rv.router, prefix="/shops", tags=["marketplace"])
router.include_router(nt.router, prefix="/notifications", tags=["marketplace"])
router.include_router(cm.router, prefix="", tags=["marketplace"])
router.include_router(rp.router, prefix="", tags=["marketplace"])
router.include_router(cn.router, prefix="", tags=["marketplace"])
