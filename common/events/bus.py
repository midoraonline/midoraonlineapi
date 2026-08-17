"""In-process pub/sub (NestJS EventEmitter2 equivalent).

Handlers run in the same request as `await bus.emit(...)`. That is
intentional on Vercel: `BackgroundTasks` / `create_task` die when the
response ships, so subscribers that must finish (inline moderation)
have to be awaited on the route.

A failing subscriber is logged and skipped so one listener cannot take
down the rest (or the HTTP response).
"""
from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event: str, handler: EventHandler) -> None:
        if handler not in self._handlers[event]:
            self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        self._handlers[event] = [h for h in handlers if h is not handler]

    async def emit(self, event: str, payload: Any = None) -> None:
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("event handler failed for %s", event)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide bus. Feature modules subscribe at app boot."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> EventBus:
    """Replace the singleton (tests)."""
    global _bus
    _bus = EventBus()
    return _bus
