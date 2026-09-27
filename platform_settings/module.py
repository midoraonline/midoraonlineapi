from common.module import AppModule, RouterSpec


class SettingsModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from platform_settings.routes import router
        return [RouterSpec(router)]
