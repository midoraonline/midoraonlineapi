from common.module import AppModule, RouterSpec


class SearchModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from search.router import router
        return [RouterSpec(router)]
