from common.module import AppModule, RouterSpec


class TenantsModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from tenants.router import router
        return [RouterSpec(router)]
