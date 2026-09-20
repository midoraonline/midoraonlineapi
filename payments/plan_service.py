from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from payments.plans import DEFAULT_PLAN, get_plan, is_valid_tier

ANALYTICS_UPGRADE_DETAIL = (
    "Shop analytics are available on the Standard plan and above. "
    "Upgrade your plan to view them."
)
ANALYTICS_UPGRADE_CODE = "plan_upgrade_required"


def _count(client: Any, table: str, column: str, value: str) -> int:
    r = client.table(table).select("id", count="exact").eq(column, value).execute()
    if hasattr(r, "count") and r.count is not None:
        return r.count
    return len(r.data or [])


def effective_plan_tier(plan_tier: Any, plan_expires_at: Any = None) -> str:
    raw = str(plan_tier or DEFAULT_PLAN).strip().lower()
    tier = raw if is_valid_tier(raw) else DEFAULT_PLAN
    if plan_expires_at:
        try:
            expiry = datetime.fromisoformat(str(plan_expires_at).replace("Z", "+00:00"))
            if expiry < datetime.now(timezone.utc):
                return DEFAULT_PLAN
        except ValueError:
            pass
    return tier


def get_user_plan_tier(client: Any, user_id: str) -> str:
    r = (
        client.table("users")
        .select("plan_tier,plan_expires_at")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not r.data:
        return DEFAULT_PLAN
    row = r.data[0]
    return effective_plan_tier(row.get("plan_tier"), row.get("plan_expires_at"))


def get_user_plan(client: Any, user_id: str) -> dict:
    return get_plan(get_user_plan_tier(client, user_id))


def assert_can_create_shop(client: Any, user_id: str) -> None:
    plan = get_user_plan(client, user_id)
    count = _count(client, "shops", "owner_id", user_id)
    if count >= plan["max_shops"]:
        raise PermissionError(
            f"Your {plan['name']} plan allows up to {plan['max_shops']} shop(s). "
            "Upgrade your plan to open more shops."
        )


def assert_can_create_product(client: Any, shop_id: str, owner_id: str) -> None:
    plan = get_user_plan(client, owner_id)
    count = _count(client, "products", "shop_id", shop_id)
    if count >= plan["max_products_per_shop"]:
        raise PermissionError(
            f"Your {plan['name']} plan allows up to {plan['max_products_per_shop']} item(s) per shop. "
            "Upgrade your plan to list more items."
        )


def has_analytics_access(client: Any, user_id: str) -> bool:
    return bool(get_user_plan(client, user_id).get("analytics_enabled"))
