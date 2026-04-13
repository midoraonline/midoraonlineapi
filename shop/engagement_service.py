from typing import Any

from core.postgrest_compat import is_undefined_column_error
from postgrest.exceptions import APIError


def _parse_rpc_int(data: Any) -> int | None:
    if data is None:
        return None
    if isinstance(data, (int, float)):
        return int(data)
    if isinstance(data, list) and data:
        return _parse_rpc_int(data[0])
    if isinstance(data, dict) and len(data) == 1:
        return _parse_rpc_int(next(iter(data.values())))
    return None


def _shop_view_count(client: Any, shop_id: str) -> int:
    try:
        r = client.table("shops").select("view_count").eq("id", shop_id).limit(1).execute()
    except APIError as exc:
        if is_undefined_column_error(exc):
            return 0
        raise
    if not r.data:
        return 0
    return int(r.data[0].get("view_count") or 0)


def _product_view_count(client: Any, product_id: str) -> int:
    try:
        r = client.table("products").select("view_count").eq("id", product_id).limit(1).execute()
    except APIError as exc:
        if is_undefined_column_error(exc):
            return 0
        raise
    if not r.data:
        return 0
    return int(r.data[0].get("view_count") or 0)


def _count_by_shop(client: Any, table: str, shop_id: str) -> int:
    r = client.table(table).select("shop_id", count="exact").eq("shop_id", shop_id).limit(1).execute()
    return int(r.count or 0)


def _count_by_product(client: Any, product_id: str) -> int:
    r = (
        client.table("product_likes")
        .select("product_id", count="exact")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )
    return int(r.count or 0)


def shop_exists(client: Any, shop_id: str) -> bool:
    r = client.table("shops").select("id").eq("id", shop_id).limit(1).execute()
    return bool(r.data)


def product_exists(client: Any, product_id: str) -> bool:
    r = client.table("products").select("id").eq("id", product_id).limit(1).execute()
    return bool(r.data)


def get_shop_engagement(client: Any, shop_id: str, viewer_user_id: str | None) -> dict[str, Any]:
    follower_count = _count_by_shop(client, "shop_follows", shop_id)
    like_count = _count_by_shop(client, "shop_likes", shop_id)
    viewer_following: bool | None = None
    viewer_liked_shop: bool | None = None
    if viewer_user_id:
        fr = (
            client.table("shop_follows")
            .select("user_id")
            .eq("shop_id", shop_id)
            .eq("user_id", viewer_user_id)
            .limit(1)
            .execute()
        )
        viewer_following = bool(fr.data)
        lr = (
            client.table("shop_likes")
            .select("user_id")
            .eq("shop_id", shop_id)
            .eq("user_id", viewer_user_id)
            .limit(1)
            .execute()
        )
        viewer_liked_shop = bool(lr.data)
    return {
        "follower_count": follower_count,
        "like_count": like_count,
        "view_count": _shop_view_count(client, shop_id),
        "viewer_following": viewer_following,
        "viewer_liked_shop": viewer_liked_shop,
    }


def get_product_engagement(client: Any, product_id: str, viewer_user_id: str | None) -> dict[str, Any]:
    like_count = _count_by_product(client, product_id)
    viewer_liked: bool | None = None
    if viewer_user_id:
        r = (
            client.table("product_likes")
            .select("user_id")
            .eq("product_id", product_id)
            .eq("user_id", viewer_user_id)
            .limit(1)
            .execute()
        )
        viewer_liked = bool(r.data)
    return {
        "like_count": like_count,
        "view_count": _product_view_count(client, product_id),
        "viewer_liked": viewer_liked,
    }


def record_shop_view(client: Any, shop_id: str) -> int:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    r = client.rpc("increment_shop_view_count", {"p_shop_id": shop_id}).execute()
    v = _parse_rpc_int(r.data)
    if v is None:
        raise ValueError("Failed to record shop view (apply migration 20260213_shop_view_counts.sql)")
    return v


def record_product_view(client: Any, product_id: str) -> int:
    if not product_exists(client, product_id):
        raise ValueError("Product not found")
    r = client.rpc("increment_product_view_count", {"p_product_id": product_id}).execute()
    v = _parse_rpc_int(r.data)
    if v is None:
        raise ValueError("Failed to record product view (apply migration 20260213_shop_view_counts.sql)")
    return v


def follow_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    client.table("shop_follows").upsert({"user_id": user_id, "shop_id": shop_id}).execute()
    return get_shop_engagement(client, shop_id, user_id)


def unfollow_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    client.table("shop_follows").delete().eq("user_id", user_id).eq("shop_id", shop_id).execute()
    return get_shop_engagement(client, shop_id, user_id)


def like_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    if not shop_exists(client, shop_id):
        raise ValueError("Shop not found")
    client.table("shop_likes").upsert({"user_id": user_id, "shop_id": shop_id}).execute()
    return get_shop_engagement(client, shop_id, user_id)


def unlike_shop(client: Any, user_id: str, shop_id: str) -> dict[str, Any]:
    client.table("shop_likes").delete().eq("user_id", user_id).eq("shop_id", shop_id).execute()
    return get_shop_engagement(client, shop_id, user_id)


def like_product(client: Any, user_id: str, product_id: str) -> dict[str, Any]:
    if not product_exists(client, product_id):
        raise ValueError("Product not found")
    client.table("product_likes").upsert({"user_id": user_id, "product_id": product_id}).execute()
    return get_product_engagement(client, product_id, user_id)


def unlike_product(client: Any, user_id: str, product_id: str) -> dict[str, Any]:
    client.table("product_likes").delete().eq("user_id", user_id).eq("product_id", product_id).execute()
    return get_product_engagement(client, product_id, user_id)
