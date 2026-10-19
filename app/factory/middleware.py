from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import get_settings
from core.request_context import bind_correlation_id, reset_correlation_id

CORRELATION_HEADER = "X-Correlation-Id"
_CORRELATION_HEADER_BYTES = CORRELATION_HEADER.lower().encode()


class CorrelationIdMiddleware:
    """Plain ASGI middleware (not BaseHTTPMiddleware) — avoids the extra
    anyio task group and response-buffering overhead BaseHTTPMiddleware adds
    on every request, and composes cleanly with streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cid = headers.get(_CORRELATION_HEADER_BYTES, b"").decode() or str(uuid.uuid4())
        scope.setdefault("state", {})["correlation_id"] = cid
        token = bind_correlation_id(cid)

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (_CORRELATION_HEADER_BYTES, cid.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_correlation_id(token)


_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(self)"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._hsts = get_settings().is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                existing = {name for name, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                if self._hsts and b"strict-transport-security" not in existing:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def register_middleware(app: FastAPI) -> None:
    settings = get_settings()
    # Compress JSON responses over ~1KB — cheap CPU trade for real bandwidth
    # savings, especially over the public internet to mobile clients.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Admin-Key",
            "X-Midora-Session",
            CORRELATION_HEADER,
        ],
        expose_headers=[CORRELATION_HEADER],
    )

