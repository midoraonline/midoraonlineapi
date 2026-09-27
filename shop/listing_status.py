"""Server-side listing status transitions.

Closing a listing (sold, unavailable, filled, closed, and the existing
hidden/expired states) always unpublishes it and zeroes stock. Bringing a
product back requires a new stock quantity. Services, jobs, opportunities,
and property do not use stock.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

CLOSING_STATUSES = frozenset(
    {"sold", "unavailable", "filled", "closed", "hidden", "expired"}
)
REACTIVATABLE_STATUSES = CLOSING_STATUSES
STOCK_REQUIRED_ITEM_TYPES = frozenset({"product"})
MERCHANT_STATUSES = CLOSING_STATUSES | {"draft", "active"}


def _http(status: int, message: str, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"detail": message, "code": code})


def stock_required(item_type: str | None) -> bool:
    return (item_type or "product").strip().lower() not in {
        "service",
        "job",
        "opportunity",
        "property",
    }


def is_closing_only(fields: dict[str, Any]) -> bool:
    """True when the patch only takes a listing down."""
    status = fields.get("status")
    if status not in CLOSING_STATUSES:
        return False
    return set(fields) <= {"status", "stock_quantity", "is_published"}


def apply_listing_status(
    payload: dict[str, Any],
    existing: dict[str, Any],
    *,
    content_changed: bool,
) -> None:
    """Mutate `payload` so status, publish, and stock stay consistent."""
    requested = payload.get("status")
    if requested is not None:
        requested = str(requested).strip().lower()
        payload["status"] = requested
        if requested not in MERCHANT_STATUSES:
            raise _http(422, "That listing status is not allowed.", "invalid_status")

    current = str(existing.get("status") or "").strip().lower()
    item_type = str(payload.get("item_type") or existing.get("item_type") or "product")

    if requested in CLOSING_STATUSES:
        payload["status"] = requested
        payload["is_published"] = False
        payload["stock_quantity"] = 0
        return

    if requested == "draft":
        payload["is_published"] = False
        return

    if content_changed:
        payload["status"] = "pending_review"
        return

    reactivating = requested == "active" or (
        payload.get("is_published") is True and current in REACTIVATABLE_STATUSES
    )
    if reactivating and current in REACTIVATABLE_STATUSES:
        if stock_required(item_type):
            if "stock_quantity" not in payload or int(payload.get("stock_quantity") or 0) <= 0:
                raise _http(
                    400,
                    "Enter a stock quantity greater than 0 before publishing this listing again.",
                    "stock_required",
                )
        payload["status"] = "active"
        payload["is_published"] = True
        return

    if requested == "active":
        payload.pop("status", None)
