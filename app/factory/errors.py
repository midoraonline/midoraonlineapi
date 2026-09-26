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
from core.request_context import RequestContext

logger = logging.getLogger(__name__)


def _envelope(
    message: str,
    code: str,
    status_code: int,
    extra: dict | None = None,
) -> JSONResponse:
    body: dict = {"detail": message, "code": code}
    for key, value in (extra or {}).items():
        if key not in body:
            body[key] = value
    return JSONResponse(status_code=status_code, content=body)


def _http_detail_parts(detail: object, status_code: int) -> tuple[str, str]:
    if isinstance(detail, dict):
        message = detail.get("detail") or detail.get("message") or "Request failed"
        code = detail.get("code") or f"http_{status_code}"
        return str(message), str(code)
    if isinstance(detail, str) and detail.strip():
        code = "rate_limited" if status_code == 429 else f"http_{status_code}"
        return detail, code
    return "Request failed", f"http_{status_code}"


def register_exception_handlers(app: FastAPI) -> None:
    settings = get_settings()

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail, code = _http_detail_parts(exc.detail, exc.status_code)
        extra = None
        if isinstance(exc.detail, dict):
            extra = {
                key: value
                for key, value in exc.detail.items()
                if key not in {"detail", "message", "code"}
            }
        response = _envelope(detail, code, exc.status_code, extra)
        if exc.headers:
            for key, value in exc.headers.items():
                response.headers[key] = value
        return response

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

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return _envelope(str(exc) or "Invalid request", "bad_request", status.HTTP_400_BAD_REQUEST)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled error on %s %s cid=%s",
            request.method,
            request.url.path,
            RequestContext.get_correlation_id(),
        )
        if settings.is_production:
            message = "Internal server error"
        else:
            message = f"{type(exc).__name__}: {exc}"
        return _envelope(message, "internal_error", status.HTTP_500_INTERNAL_SERVER_ERROR)
