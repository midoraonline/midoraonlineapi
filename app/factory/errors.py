"""Centralised exception handlers.

Shapes every error into a stable JSON envelope: `{"detail": <message>, "code": <slug>}`.
This keeps the frontend error handling predictable across validation errors,
HTTP exceptions and unexpected server crashes, without leaking internals in
production.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import get_settings

logger = logging.getLogger(__name__)


def _envelope(message: str, code: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message, "code": code})


def register_exception_handlers(app: FastAPI) -> None:
    settings = get_settings()

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _envelope(detail, f"http_{exc.status_code}", exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Invalid request payload",
                "code": "validation_error",
                "errors": exc.errors(),
            },
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(_: Request, exc: PermissionError) -> JSONResponse:
        return _envelope(str(exc) or "Forbidden", "forbidden", status.HTTP_403_FORBIDDEN)

    @app.exception_handler(LookupError)
    async def lookup_error_handler(_: Request, exc: LookupError) -> JSONResponse:
        return _envelope(str(exc) or "Not found", "not_found", status.HTTP_404_NOT_FOUND)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        if settings.is_production:
            message = "Internal server error"
        else:
            message = f"{type(exc).__name__}: {exc}"
        return _envelope(message, "internal_error", status.HTTP_500_INTERNAL_SERVER_ERROR)
