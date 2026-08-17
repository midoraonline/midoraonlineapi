"""Shared, domain-agnostic code.

Event bus, module protocol, and other Nest-style primitives used across
feature modules. Domain logic stays in those modules; infrastructure
(config, DB client, schemas) lives in `core`.
"""

from common.events.bus import EventBus, get_event_bus
from common.events.names import Events
from common.events.payloads import (
    ProductPostedEvent,
    ProductStatusChangedEvent,
    ShopVerificationChangedEvent,
)
from common.module import AppModule, RouterSpec

__all__ = [
    "AppModule",
    "EventBus",
    "Events",
    "ProductPostedEvent",
    "ProductStatusChangedEvent",
    "ShopVerificationChangedEvent",
    "RouterSpec",
    "get_event_bus",
]

