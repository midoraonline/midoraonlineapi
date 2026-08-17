from common.module import AppModule, RouterSpec


class MarketplaceModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from marketplace.router import router
        return [RouterSpec(router)]
