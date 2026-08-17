from common.module import AppModule, RouterSpec


class AiModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from ai.router import router
        return [RouterSpec(router)]
