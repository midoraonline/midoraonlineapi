from common.module import AppModule, RouterSpec


class CategoriesModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from categories.router import router
        return [RouterSpec(router)]
