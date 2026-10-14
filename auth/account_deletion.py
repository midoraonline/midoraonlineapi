"""Delete (or anonymize) a user account and everything they own.

Midora uses its own users table (not Supabase Auth). Many tables reference
users without ON DELETE CASCADE (chat, orders, reviews), so a hard delete is
attempted first and, if a foreign key blocks it, the row is anonymized and
marked status='deleted' instead. Shops and listings are deleted the same
way; UploadThing files that nothing else uses are removed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


class DeletionError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - best effort, logged
        logger.info("account deletion: %s skipped: %s", label, exc)
        return None


def _is_last_admin(client, user_id: str) -> bool:
    rows = (
        client.table("users")
        .select("id")
        .eq("user_role", "admin")
        .neq("status", "deleted")
        .execute()
        .data
        or []
    )
    ids = {str(r["id"]) for r in rows}
    return ids == {user_id}


def _delete_shop(client, shop: dict, removed_keys: list[str]) -> str:
    from media.keys import file_key_from_url
    from media.references import keys_on_product, shop_banner_url

    shop_id = str(shop["id"])
    products = (
        _safe(
            "products read",
            lambda: client.table("products")
            .select("id,image_urls,image_keys,video_keys")
            .eq("shop_id", shop_id)
            .execute()
            .data,
        )
        or []
    )
    for row in products:
        removed_keys.extend(keys_on_product(row))
    for url in (shop.get("logo_url"), shop_banner_url(shop.get("theme_config"))):
        key = file_key_from_url(url or "")
        if key:
            removed_keys.append(key)

    try:
        client.table("products").delete().eq("shop_id", shop_id).execute()
    except Exception as exc:  # FK from orders/messages etc.
        logger.info("products hard delete blocked for shop %s: %s", shop_id, exc)
        _safe(
            "products archive",
            lambda: client.table("products")
            .update({"is_published": False, "status": "hidden", "image_urls": [], "description": None})
            .eq("shop_id", shop_id)
            .execute(),
        )
    try:
        client.table("shops").delete().eq("id", shop_id).execute()
        return "deleted"
    except Exception as exc:
        logger.info("shop hard delete blocked for %s: %s", shop_id, exc)
        client.table("shops").update(
            {
                "is_active": False,
                "name": "Deleted business",
                "slug": f"deleted-{shop_id}",
                "description": None,
                "about": None,
                "logo_url": None,
                "shop_email": None,
                "whatsapp_number": None,
                "contacts": [],
                "social_links": [],
            }
        ).eq("id", shop_id).execute()
        return "anonymized"


def _delete_supabase_auth_user(client, user_id: str) -> str:
    """Remove a matching Supabase Auth user, if one exists (service role)."""
    try:
        client.auth.admin.delete_user(user_id)
        return "deleted"
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "not found" in text or "404" in text or "user_not_found" in text:
            return "none"
        logger.warning("supabase auth delete failed for %s: %s", user_id, exc)
        return "failed"


async def delete_account(
    user_id: str,
    *,
    actor_id: str | None = None,
    client=None,
    delete_files: Callable[[list[str]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    client = client or get_supabase_admin()
    rows = (
        client.table("users")
        .select("id,email,user_role,status")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise DeletionError("Account not found", "not_found")
    user = rows[0]
    if user.get("status") == "deleted":
        raise DeletionError("Account is already deleted", "already_deleted")
    if user.get("user_role") == "admin" and _is_last_admin(client, user_id):
        raise DeletionError("You can't delete the last admin account.", "last_admin")

    from media.keys import file_key_from_url

    removed_keys: list[str] = []
    summary: dict[str, Any] = {"shops_deleted": 0, "shops_anonymized": 0}

    shops = (
        _safe(
            "shops read",
            lambda: client.table("shops")
            .select("id,logo_url,theme_config")
            .eq("owner_id", user_id)
            .execute()
            .data,
        )
        or []
    )
    for shop in shops:
        outcome = _delete_shop(client, shop, removed_keys)
        summary["shops_deleted" if outcome == "deleted" else "shops_anonymized"] += 1

    profile = _safe(
        "profile read",
        lambda: client.table("profiles").select("avatar_url").eq("id", user_id).limit(1).execute().data,
    ) or []
    if profile:
        key = file_key_from_url(profile[0].get("avatar_url") or "")
        if key:
            removed_keys.append(key)

    uploads = _safe(
        "media_uploads read",
        lambda: client.table("media_uploads").select("key").eq("user_id", user_id).execute().data,
    ) or []
    removed_keys.extend(str(u["key"]) for u in uploads if u.get("key"))

    # Sessions and personal side tables.
    _safe(
        "refresh tokens",
        lambda: client.table("refresh_tokens").update({"revoked_at": _now()}).eq("user_id", user_id).is_("revoked_at", "null").execute(),
    )
    for table, column in (
        ("push_subscriptions", "user_id"),
        ("search_history", "user_id"),
        ("password_reset_tokens", "user_id"),
        ("email_verification_tokens", "user_id"),
        ("media_uploads", "user_id"),
    ):
        _safe(f"{table} delete", lambda t=table, c=column: client.table(t).delete().eq(c, user_id).execute())

    _safe(
        "profile anonymize",
        lambda: client.table("profiles")
        .update({"full_name": None, "avatar_url": None, "phone_number": None})
        .eq("id", user_id)
        .execute(),
    )
    _safe("profile bio", lambda: client.table("profiles").update({"bio": None}).eq("id", user_id).execute())

    try:
        _safe("profile delete", lambda: client.table("profiles").delete().eq("id", user_id).execute())
        client.table("users").delete().eq("id", user_id).execute()
        summary["user"] = "deleted"
    except Exception as exc:
        logger.info("user hard delete blocked for %s: %s", user_id, exc)
        client.table("users").update(
            {
                "email": f"deleted-{user_id}@deleted.midora.invalid",
                "password_hash": "!deleted",
                "full_name": "Deleted user",
                "phone_number": None,
                "phone_verified": False,
                "email_verified": False,
                "status": "deleted",
                "deleted_at": _now(),
            }
        ).eq("id", user_id).execute()
        summary["user"] = "anonymized"

    summary["supabase_auth"] = _delete_supabase_auth_user(client, user_id)

    if removed_keys:
        from media.references import keys_safe_to_delete, load_referenced_keys
        from media.uploadthing import delete_file_keys

        try:
            referenced = load_referenced_keys(client)
            safe = keys_safe_to_delete(removed_keys, referenced)
            if safe:
                await (delete_files or delete_file_keys)(safe)
            summary["files_deleted"] = len(safe)
        except Exception:
            logger.exception("media cleanup failed for deleted user %s", user_id)
            summary["files_deleted"] = 0

    logger.info("account deleted user=%s actor=%s summary=%s", user_id, actor_id or user_id, summary)
    return summary
