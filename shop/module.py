"""Shop module — products, orders, engagement.

Emits product lifecycle events from the write routes. Does not subscribe:
side effects (moderation, mail, ranking, embeddings) live in other modules.
"""
from common.module import AppModule, RouterSpec


class ShopModule(AppModule):
    def routers(self) -> list[RouterSpec]:
        from shop.router import router
        return [RouterSpec(router)]
