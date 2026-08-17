from common.events import EventBus, Events
from common.module import AppModule, RouterSpec
from notifications.listeners import (
    on_product_status_changed,
    on_shop_verification_changed,
)


class PushModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from notifications.router import router
        return [RouterSpec(router, prefix="/api/v1/push", tags=["push"])]

    def register_listeners(self, bus: EventBus) -> None:
        bus.on(Events.PRODUCT_STATUS_CHANGED, on_product_status_changed)
        bus.on(Events.SHOP_VERIFICATION_CHANGED, on_shop_verification_changed)
