from fastapi import APIRouter, Depends

from core.security import require_admin
from analytics.routes import ingest, insights

router = APIRouter()

router.include_router(ingest.router, prefix="/analytics", tags=["analytics"])

# Admin-only insight queries — never exposed to browsers directly.
router.include_router(
    insights.router,
    prefix="/admin/analytics",
    tags=["analytics", "admin"],
    dependencies=[Depends(require_admin)],
)
