from datetime import datetime, timezone

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    async def health() -> dict:
        return {
            "status": "ok",
            "service": app.title,
            "version": app.version,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    app.add_api_route("/api/v1/health", health, methods=["GET"], tags=["health"])

    # Lazy imports to avoid circular imports
    from auth.router import router as auth_router
    from tenants.router import router as tenants_router
    from shop.router import router as shop_router
    from ai.router import router as ai_router
    from payments.router import router as payments_router
    from admin.router import router as admin_router
    from categories.router import router as categories_router
    from feed.router import router as feed_router
    from marketplace.router import router as marketplace_router
    from mail.routes.contactus import router as contactus_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(categories_router, prefix="/api/v1")
    app.include_router(tenants_router, prefix="/api/v1")
    app.include_router(shop_router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    app.include_router(payments_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(feed_router, prefix="/api/v1")
    app.include_router(marketplace_router, prefix="/api/v1")
    app.include_router(contactus_router, prefix="/api/v1")
