from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    # Lazy imports to avoid circular imports
    from auth.router import router as auth_router
    from tenants.router import router as tenants_router
    from shop.router import router as shop_router
    from ai.router import router as ai_router
    from payments.router import router as payments_router
    from admin.router import router as admin_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(tenants_router, prefix="/api/v1")
    app.include_router(shop_router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    app.include_router(payments_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
