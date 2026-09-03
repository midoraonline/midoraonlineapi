from common.module import AppModule, RouterSpec


class AnalyticsModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from analytics.router import router
        return [RouterSpec(router)]
