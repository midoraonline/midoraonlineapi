from common.events import EventBus, Events
from common.module import AppModule, RouterSpec
from listingModeration.listeners import (
    on_product_moderate_now,
    on_product_pending_review,
)


class ListingModerationModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from listingModeration.router import router
        return [RouterSpec(router, prefix="/api/v1/moderation", tags=["moderation"])]

    def register_listeners(self, bus: EventBus) -> None:
        bus.on(Events.PRODUCT_PENDING_REVIEW, on_product_pending_review)
        bus.on(Events.PRODUCT_MODERATE_NOW, on_product_moderate_now)

    def on_startup(self) -> None:
        from listingModeration.worker import start_worker
        start_worker()

    async def on_shutdown(self) -> None:
        from listingModeration.worker import stop_worker
        await stop_worker()
