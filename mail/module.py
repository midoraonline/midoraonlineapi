from common.events import EventBus, Events
from common.module import AppModule, RouterSpec
from mail.listeners import (
    on_product_created,
    on_product_status_changed,
    on_shop_verification_changed,
)


class MailModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from mail.routes.contactus import router
        return [RouterSpec(router)]

    def register_listeners(self, bus: EventBus) -> None:
        bus.on(Events.PRODUCT_CREATED, on_product_created)
        bus.on(Events.PRODUCT_STATUS_CHANGED, on_product_status_changed)
        bus.on(Events.SHOP_VERIFICATION_CHANGED, on_shop_verification_changed)


    def on_startup(self) -> None:
        from mail.queue import start_worker
        start_worker()

    async def on_shutdown(self) -> None:
        from mail.queue import stop_worker
        await stop_worker()
