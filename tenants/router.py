from fastapi import APIRouter

from tenants.routes import discovery, shops

router = APIRouter(prefix="/shops", tags=["shops"])
router.include_router(shops.router)
router.include_router(discovery.router)
