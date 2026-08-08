from fastapi import APIRouter

from .routes import drain, status, submit

router = APIRouter(tags=["moderation"])

router.include_router(submit.router, prefix="")
router.include_router(status.router, prefix="")
router.include_router(drain.router, prefix="")
