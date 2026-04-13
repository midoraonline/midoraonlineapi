from fastapi import APIRouter

from auth.routes import google, login, register, session, verify

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(register.router)
router.include_router(login.router)
router.include_router(google.router)
router.include_router(session.router)
router.include_router(verify.router)
