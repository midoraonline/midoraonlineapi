"""Request-scoped identity without threading it through every call.

Set in HTTP middleware / auth dependencies; read from services and logs.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


class RequestContext:
    @staticmethod
    def get_correlation_id() -> str | None:
        return _correlation_id.get()

    @staticmethod
    def get_current_user_id() -> str | None:
        return _user_id.get()


def bind_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def bind_user_id(value: str | None) -> None:
    _user_id.set(value)
