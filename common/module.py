"""NestJS-style feature module contract for FastAPI.

Each domain folder (shop, listingModeration, mail, …) exposes an `AppModule`
subclass that declares:

    * routers to mount
    * event subscribers (`register_listeners`)
    * optional process-lifetime hooks (`on_startup` / `on_shutdown`)

The app factory iterates the module list once — no more ad-hoc imports
scattered through route handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from common.events.bus import EventBus


@dataclass(frozen=True)
class RouterSpec:
    router: APIRouter
    prefix: str = "/api/v1"
    tags: list[str] = field(default_factory=list)


class AppModule:
    """Base class every feature module subclasses.

    Override only what the module needs. Empty defaults mean a module can
    be routers-only, listeners-only, or both.
    """

    def routers(self) -> list[RouterSpec]:
        return []

    def register_listeners(self, bus: EventBus) -> None:
        return None

    def on_startup(self) -> None:
        """Called from lifespan on long-lived (non-serverless) processes."""
        return None

    async def on_shutdown(self) -> None:
        return None
