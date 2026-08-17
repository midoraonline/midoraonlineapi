from common.module import AppModule, RouterSpec


class PaymentsModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from payments.router import router
        return [RouterSpec(router)]
