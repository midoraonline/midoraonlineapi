"""Public seller fields. Personal profiles read name, joined, and badges from the user."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def apply_seller_profile(shop: dict[str, Any], user: dict | None) -> None:
    is_personal = bool(shop.get("is_personal"))
    shop["is_personal"] = is_personal
    if not is_personal:
        shop.setdefault("seller_name", shop.get("name"))
        shop.setdefault("joined_at", _iso(shop.get("created_at")))
        shop.setdefault("last_active_at", _iso(shop.get("last_seen_at")))
        return

    user = user or {}
    name = (user.get("full_name") or "").strip() or shop.get("name") or "Seller"
    badges = [
        b for b in (shop.get("trust_badges") or [])
        if isinstance(b, str) and b != "shop_listed"
    ]
    if user.get("phone_verified") and "phone_verified" not in badges:
        badges.append("phone_verified")
    joined = _iso(user.get("created_at")) or _iso(shop.get("created_at"))
    last_active = _iso(user.get("last_seen_at")) or _iso(shop.get("last_seen_at"))
    shop["name"] = name
    shop["seller_name"] = name
    shop["joined_at"] = joined
    shop["last_active_at"] = last_active
    shop["created_at"] = joined
    shop["last_seen_at"] = last_active
    shop["trust_badges"] = badges
    if not shop.get("logo_url") and user.get("avatar_url"):
        shop["logo_url"] = user.get("avatar_url")
    if user.get("phone_verified") and user.get("phone_number") and not shop.get("whatsapp_number"):
        shop["whatsapp_number"] = user.get("phone_number")


def _load_users(client: Any, user_ids: list[str]) -> dict[str, dict]:
    if not user_ids:
        return {}
    users: dict[str, dict] = {}
    try:
        r = (
            client.table("users")
            .select("id,full_name,phone_number,phone_verified,created_at,last_seen_at")
            .in_("id", user_ids)
            .execute()
        )
        for row in r.data or []:
            if row.get("id"):
                users[str(row["id"])] = row
    except Exception as exc:
        logger.warning("seller user lookup failed: %s", exc)
        return {}
    try:
        pr = (
            client.table("profiles")
            .select("id,full_name,avatar_url")
            .in_("id", user_ids)
            .execute()
        )
        for row in pr.data or []:
            uid = str(row.get("id") or "")
            if not uid:
                continue
            user = users.setdefault(uid, {"id": uid})
            if row.get("full_name") and not user.get("full_name"):
                user["full_name"] = row.get("full_name")
            if row.get("avatar_url"):
                user["avatar_url"] = row.get("avatar_url")
    except Exception as exc:
        logger.warning("seller profile lookup failed: %s", exc)
    return users


def overlay_personal_sellers(client: Any, shops: dict[str, dict]) -> None:
    """Fill seller_name, joined_at, last_active_at, and badges. Mutates shop dicts."""
    owner_ids = list({
        str(shop.get("owner_id"))
        for shop in shops.values()
        if shop.get("is_personal") and shop.get("owner_id")
    })
    users = _load_users(client, owner_ids) if owner_ids else {}
    for shop in shops.values():
        owner = str(shop.get("owner_id") or "")
        apply_seller_profile(shop, users.get(owner))
