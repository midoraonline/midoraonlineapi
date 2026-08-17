from common.module import AppModule, RouterSpec


class AuthModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from auth.router import router
        return [RouterSpec(router)]
