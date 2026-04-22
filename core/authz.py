"""Authorization helpers shared across feature modules.

Keep these pure: they only read from the passed Supabase client and raise
`PermissionError` when ownership fails. Routes convert the exception to
HTTP 403 via the shared handler in `app/factory/middleware.py`.
"""
from typing import Any


def _get_shop_owner(client: Any, shop_id: str) -> str | None:
    r = client.table("shops").select("owner_id").eq("id", shop_id).limit(1).execute()
    if not r.data:
        return None
    return str(r.data[0].get("owner_id") or "")


def ensure_shop_owner(client: Any, shop_id: str, user_id: str) -> None:
    """Raise `LookupError` if the shop does not exist, `PermissionError` if user is not the owner."""
    owner_id = _get_shop_owner(client, shop_id)
    if owner_id is None:
        raise LookupError("Shop not found")
    if owner_id != user_id:
        raise PermissionError("You do not own this shop")


def ensure_product_owner(client: Any, product_id: str, user_id: str) -> str:
    """Return the `shop_id` the product belongs to. Raises like `ensure_shop_owner`."""
    r = client.table("products").select("shop_id").eq("id", product_id).limit(1).execute()
    if not r.data:
        raise LookupError("Product not found")
    shop_id = str(r.data[0].get("shop_id") or "")
    ensure_shop_owner(client, shop_id, user_id)
    return shop_id
