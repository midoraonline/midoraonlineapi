from common.events import EventBus, Events
from common.module import AppModule, RouterSpec
from feed.listeners import on_product_posted


class FeedModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from feed.router import router
        return [RouterSpec(router)]

    def register_listeners(self, bus: EventBus) -> None:
        bus.on(Events.PRODUCT_CREATED, on_product_posted)
        bus.on(Events.PRODUCT_UPDATED, on_product_posted)
