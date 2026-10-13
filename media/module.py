from common.module import AppModule, RouterSpec


class MediaModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from media.routes import cron_router, user_router

        return [
            RouterSpec(cron_router, prefix="/api/v1/media", tags=["media"]),
            RouterSpec(user_router, prefix="/api/v1/media", tags=["media"]),
        ]
