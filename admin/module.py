from common.module import AppModule, RouterSpec


class AdminModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from admin.router import router
        return [RouterSpec(router)]
