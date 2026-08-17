from common.events import EventBus, Events
from common.module import AppModule
from ranking.listeners import on_product_posted


class RankingModule(AppModule):
    def register_listeners(self, bus: EventBus) -> None:
        bus.on(Events.PRODUCT_CREATED, on_product_posted)
        bus.on(Events.PRODUCT_UPDATED, on_product_posted)
