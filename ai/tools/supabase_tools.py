from __future__ import annotations

import logging
from typing import Any

from decimal import Decimal

from db.supabase import get_supabase_admin

logger = logging.getLogger(__name__)


def _safe_truncate(text: str, max_chars: int = 4000) -> str:
    """Prevent tool payloads from getting too large."""
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n...[truncated]"


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y", "on"):
            return True
        if v in ("false", "0", "no", "n", "off"):
            return False
    return default


def _json_safe(value: Any) -> Any:
    """Convert Supabase-returned values into JSON-serializable primitives."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def pull_shop_catalog(shop_id: str, max_products: int = 50) -> dict[str, Any]:
    """
    Pull shop and product data from Supabase (read-only).

    Use this when you need to answer a shop-specific question and you
    don't want to write any data into `shop_ai_context`.
    """
    max_products = _coerce_int(max_products, default=50)
    client = get_supabase_admin()

    try:
        shop_res = (
            client.table("shops")
            .select(
                "id,name,description,about,logo_url,shop_email,whatsapp_number,contacts,social_links,location,availability,shop_type"
            )
            .eq("id", shop_id)
            .limit(1)
            .execute()
        )
        shop = (shop_res.data or [{}])[0]

        products_res = (
            client.table("products")
            .select(
                "id,item_type,title,description,price_ugx,stock_quantity,image_urls,category,is_published,ai_seo_tags"
            )
            .eq("shop_id", shop_id)
            .order("created_at", desc=True)
            .limit(max_products)
            .execute()
        )
        products = products_res.data or []
    except Exception as e:
        logger.exception("pull_shop_catalog failed shop_id=%s", shop_id)
        return {
            "ok": False,
            "shop_id": shop_id,
            "error": f"supabase_read_failed: {e}",
        }

    content = []
    if shop:
        content.append(
            f"Shop: {shop.get('name')} ({shop.get('shop_type')})\n"
            f"Description: {shop.get('description') or ''}\n"
            f"About: {shop.get('about') or ''}\n"
            f"Email: {shop.get('shop_email') or ''}\n"
            f"WhatsApp: {shop.get('whatsapp_number') or ''}\n"
            f"Location: {shop.get('location') or ''}\n"
            f"Availability: {shop.get('availability') or ''}\n"
        )

    content.append("Products:")
    for p in products:
        title = p.get("title") or ""
        item_type = p.get("item_type") or "product"
        price = p.get("price_ugx")
        stock = p.get("stock_quantity")
        description = p.get("description") or ""
        content.append(
            f"- {title} ({item_type}) | Price: {price} UGX | Stock: {stock}\n"
            f"  Description: {description}"
        )

    safe_shop = _json_safe(shop)
    safe_products = _json_safe(products)

    return {
        "ok": True,
        "shop_id": shop_id,
        "shop_found": bool(shop),
        "products_found": len(products),
        "text": _safe_truncate("\n".join(content)),
        "shop": safe_shop,
        "products": safe_products,
    }


def ingest_shop_ai_context(
    shop_id: str,
    replace_existing: bool = True,
    max_products: int = 50,
) -> dict[str, Any]:
    """
    Pull shop + product data from Supabase and write summaries into
    `shop_ai_context` so the concierge can answer later.

    This is the "ingest" tool. Use it when the user asks for product lists,
    pricing/catalog details, shipping/contact policies, or when shop context
    is likely missing/stale.
    """
    client = get_supabase_admin()
    replace_existing = _coerce_bool(replace_existing, default=True)
    max_products = _coerce_int(max_products, default=50)

    cat = pull_shop_catalog(shop_id=shop_id, max_products=max_products)
    if not cat.get("ok", False):
        logger.warning(
            "ingest_shop_ai_context catalog pull failed shop_id=%s error=%s",
            shop_id,
            cat.get("error"),
        )
        return {"ok": False, "shop_id": shop_id, "error": cat.get("error") or "catalog_pull_failed"}

    shop = cat.get("shop") or {}
    products = cat.get("products") or []

    context_types = ["brand_voice", "faq", "policy"]
    if replace_existing:
        try:
            for context_type in context_types:
                client.table("shop_ai_context").delete().eq("shop_id", shop_id).eq(
                    "context_type", context_type
                ).execute()
        except Exception as e:
            logger.exception("ingest_shop_ai_context delete failed shop_id=%s", shop_id)
            return {"ok": False, "shop_id": shop_id, "error": f"context_delete_failed: {e}"}

    # Brand voice / shop identity
    brand_voice_text = _safe_truncate(
        "\n".join(
            [
                f"Shop name: {shop.get('name') or ''}",
                f"Shop type: {shop.get('shop_type') or ''}",
                f"Description: {shop.get('description') or ''}",
                f"About: {shop.get('about') or ''}",
                f"Logo: {shop.get('logo_url') or ''}",
                f"Contact email: {shop.get('shop_email') or ''}",
                f"WhatsApp: {shop.get('whatsapp_number') or ''}",
                f"Location: {shop.get('location') or ''}",
                f"Availability: {shop.get('availability') or ''}",
            ]
        )
    )

    # FAQ / catalog style (the concierge can paraphrase this into answers)
    faq_lines: list[str] = []
    for p in products:
        title = p.get("title") or ""
        item_type = p.get("item_type") or "product"
        price = p.get("price_ugx")
        stock = p.get("stock_quantity")
        description = p.get("description") or ""
        category = p.get("category") or ""
        faq_lines.append(
            f"{title} ({item_type})"
            f" - Price: {price} UGX"
            f" - Stock: {stock}"
            f"{' - Category: ' + category if category else ''}"
            f"\n  Details: {description}"
        )
    faq_text = _safe_truncate("Product catalog:\n" + "\n\n".join(faq_lines) if faq_lines else "No products found.")

    # Policy / operational info we can anchor on
    policy_text = _safe_truncate(
        "\n".join(
            [
                "Customer support & contact:",
                f"- Email: {shop.get('shop_email') or ''}",
                f"- WhatsApp: {shop.get('whatsapp_number') or ''}",
                f"- Contacts: {shop.get('contacts') or ''}",
                f"- Social links: {shop.get('social_links') or ''}",
                "",
                "Operational details:",
                f"- Location: {shop.get('location') or ''}",
                f"- Availability: {shop.get('availability') or ''}",
            ]
        )
    )

    inserted: dict[str, Any] = {}
    try:
        for context_type, content in [
            ("brand_voice", brand_voice_text),
            ("faq", faq_text),
            ("policy", policy_text),
        ]:
            res = (
                client.table("shop_ai_context")
                .insert(
                    {"shop_id": shop_id, "context_type": context_type, "content": content}
                )
                .execute()
            )
            inserted[context_type] = {
                "rows": len(res.data or []),
                "content_chars": len(content),
            }
    except Exception as e:
        logger.exception("ingest_shop_ai_context insert failed shop_id=%s", shop_id)
        return {"ok": False, "shop_id": shop_id, "error": f"context_insert_failed: {e}"}

    return {
        "ok": True,
        "shop_id": shop_id,
        "shop_found": cat.get("shop_found"),
        "products_found": cat.get("products_found"),
        "inserted": inserted,
        "text_preview": _safe_truncate(cat.get("text") or ""),
    }

