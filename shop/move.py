"""Move a listing between the owner's shops, or to "Other" (the hidden personal profile)."""

from __future__ import annotations

from typing import Any

from core.authz import ensure_product_owner, ensure_shop_owner
from feed.stages import lane_of
from payments import plan_service
from shop.personal import get_or_create_personal_shop

# Opportunities fit any shop; products and services need a shop that sells them.
_SHOP_TYPES_FOR_LANE = {"product": {"product", "both"}, "service": {"service", "both"}}


class MoveError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def shop_accepts(shop_type: str | None, item_type: str | None) -> bool:
    allowed = _SHOP_TYPES_FOR_LANE.get(lane_of(item_type))
    return True if allowed is None else (shop_type or "product") in allowed


def move_listing(client: Any, product_id: str, user_id: str, target_shop_id: str | None) -> str:
    """Move `product_id` to `target_shop_id` (None = Other). Returns the new shop id.

    Raises LookupError / PermissionError for missing or foreign rows, MoveError for rule failures.
    """
    current_shop_id = ensure_product_owner(client, product_id, user_id)
    row = client.table("products").select("item_type").eq("id", product_id).limit(1).execute()
    item_type = (row.data or [{}])[0].get("item_type")

    if target_shop_id is None:
        target, _ = get_or_create_personal_shop(client, user_id)
    else:
        target = str(target_shop_id)
        ensure_shop_owner(client, target, user_id)
        shop = client.table("shops").select("shop_type,is_personal").eq("id", target).limit(1).execute()
        info = (shop.data or [{}])[0]
        if info.get("is_personal"):
            raise MoveError("Choose \"Other\" to move a listing out of your shops.", "use_other")
        if not shop_accepts(info.get("shop_type"), item_type):
            raise MoveError(
                f"This shop does not take {lane_of(item_type)} listings. Pick a shop that does, or Other.",
                "shop_type_mismatch",
            )

    if target == current_shop_id:
        return target
    try:
        plan_service.assert_shop_has_room(client, target, user_id)
    except PermissionError as exc:
        raise MoveError(str(exc), "plan_limit") from exc

    client.table("products").update({"shop_id": target}).eq("id", product_id).execute()
    return target
