"""Hidden personal seller profile used to publish before a real shop exists."""

from __future__ import annotations

import logging
import re
from typing import Any

from postgrest.exceptions import APIError

from core.postgrest_compat import is_undefined_column_error

logger = logging.getLogger(__name__)


class ShopChoiceRequired(Exception):
    """The user has more than one real shop and must pick one."""


class PersonalShopUnavailable(Exception):
    """Migration 043 has not been applied."""


def find_personal_shop(client: Any, owner_id: str) -> dict | None:
    try:
        r = (
            client.table("shops")
            .select("id,owner_id,is_personal")
            .eq("owner_id", owner_id)
            .eq("is_personal", True)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        if is_undefined_column_error(exc, "is_personal"):
            return None
        raise
    if not r.data:
        return None
    return r.data[0]


def _slug_for(owner_id: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", owner_id.lower())[:24] or "user"
    return f"seller-{compact}"


def _seller_name(client: Any, owner_id: str) -> tuple[str, str | None, bool]:
    try:
        r = (
            client.table("users")
            .select("full_name,phone_number,phone_verified")
            .eq("id", owner_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("personal shop user lookup failed: %s", exc)
        return "Seller", None, False
    row = r.data[0] if r.data else {}
    name = (row.get("full_name") or "").strip() or "Seller"
    phone = (row.get("phone_number") or "").strip() or None
    verified = bool(row.get("phone_verified"))
    return name[:120], phone, verified


def get_or_create_personal_shop(client: Any, owner_id: str) -> tuple[str, bool]:
    """Return (shop_id, created). Reuses the owner's personal profile."""
    existing = find_personal_shop(client, owner_id)
    if existing and existing.get("id"):
        return str(existing["id"]), False

    name, phone, verified = _seller_name(client, owner_id)
    payload: dict[str, Any] = {
        "owner_id": owner_id,
        "name": name,
        "slug": _slug_for(owner_id),
        "shop_type": "both",
        "is_active": False,
        "is_personal": True,
        "trust_badges": [],
    }
    if verified and phone:
        payload["whatsapp_number"] = phone

    try:
        r = client.table("shops").insert(payload).execute()
    except APIError as exc:
        if is_undefined_column_error(exc, "is_personal"):
            raise PersonalShopUnavailable() from exc
        if getattr(exc, "code", None) == "23505":
            again = find_personal_shop(client, owner_id)
            if again and again.get("id"):
                return str(again["id"]), False
        raise
    if not r.data:
        raise ValueError("Failed to create seller profile")
    return str(r.data[0]["id"]), True


def resolve_shop_for_direct_post(client: Any, owner_id: str) -> tuple[str, bool]:
    """Shop id for a listing posted without a shop id.

    Returns (shop_id, created_personal).
    One real shop is reused. Several real shops require an explicit shop id.
    """
    personal = find_personal_shop(client, owner_id)
    if personal and personal.get("id"):
        return str(personal["id"]), False

    try:
        r = (
            client.table("shops")
            .select("id,is_personal")
            .eq("owner_id", owner_id)
            .execute()
        )
    except APIError as exc:
        if is_undefined_column_error(exc, "is_personal"):
            raise PersonalShopUnavailable() from exc
        raise
    real = [
        row for row in (r.data or [])
        if row.get("id") and not row.get("is_personal")
    ]
    if not real:
        return get_or_create_personal_shop(client, owner_id)
    if len(real) == 1:
        return str(real[0]["id"]), False
    raise ShopChoiceRequired()
