"""Nest-style module registry + health route.

Import order of listener modules is the subscriber order for a given event.
Product write side effects are split across events so moderation (slow)
never blocks mail/ranking/embeddings — those subscribe to `product.created`
/ `product.updated`; the pipeline subscribes to `product.pending_review`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

from common.events import get_event_bus
from common.module import AppModule
from core.runtime import is_serverless


def get_modules() -> list[AppModule]:
    from admin.module import AdminModule
    from ai.module import AiModule
    from auth.module import AuthModule
    from categories.module import CategoriesModule
    from feed.module import FeedModule
    from listingModeration.module import ListingModerationModule
    from mail.module import MailModule
    from marketplace.module import MarketplaceModule
    from notifications.module import PushModule
    from payments.module import PaymentsModule
    from ranking.module import RankingModule
    from search.module import SearchModule
    from shop.module import ShopModule
    from tenants.module import TenantsModule

    return [
        AuthModule(),
        CategoriesModule(),
        TenantsModule(),
        ShopModule(),
        MailModule(),
        RankingModule(),
        FeedModule(),
        ListingModerationModule(),
        AiModule(),
        PaymentsModule(),
        AdminModule(),
        SearchModule(),
        MarketplaceModule(),
        PushModule(),
    ]


def register_modules(app: FastAPI, modules: list[AppModule] | None = None) -> list[AppModule]:
    bus = get_event_bus()
    app.state.events = bus
    resolved = modules if modules is not None else get_modules()
    for module in resolved:
        module.register_listeners(bus)
        for spec in module.routers():
            kwargs: dict = {"prefix": spec.prefix}
            if spec.tags:
                kwargs["tags"] = spec.tags
            app.include_router(spec.router, **kwargs)
    return resolved


def start_module_workers(modules: list[AppModule]) -> list[AppModule]:
    """Start in-process workers. No-op on Vercel (cron drains instead)."""
    if is_serverless():
        return []
    started: list[AppModule] = []
    for module in modules:
        module.on_startup()
        started.append(module)
    return started


def register_routers(app: FastAPI) -> None:
    async def health() -> dict:
        return {
            "status": "ok",
            "service": app.title,
            "version": app.version,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    app.add_api_route("/api/v1/health", health, methods=["GET"], tags=["health"])
    app.state.modules = register_modules(app)
