"""Automatic check for a new shop: name, text and logo/banner go through the listing
moderation stages. A clean shop goes live immediately; anything else waits for an admin."""
from __future__ import annotations

import logging
from typing import Any

from .config import config
from .pipeline import _decide_from_scores
from .schemas import ModerationDecision, ModerationStatus
from .stages import image_moderation, keywords, metadata, openai_moderation, phash, profanity, text_moderation

logger = logging.getLogger(__name__)

_SHOP_COLS = "id,owner_id,name,description,about,category,logo_url,theme_config,is_active,is_personal"


def _banner_url(theme: Any) -> str | None:
    meta = theme.get("metadata") if isinstance(theme, dict) else None
    url = meta.get("banner_url") if isinstance(meta, dict) else None
    return url.strip() if isinstance(url, str) and url.strip() else None


def shop_text(shop: dict[str, Any]) -> tuple[str, str]:
    title = str(shop.get("name") or "").strip()
    body = " \n".join(
        str(shop.get(k) or "").strip() for k in ("description", "about", "category") if shop.get(k)
    )
    return title, body


def shop_images(shop: dict[str, Any]) -> list[str]:
    return [u for u in (str(shop.get("logo_url") or "").strip(), _banner_url(shop.get("theme_config"))) if u]


async def moderate_shop(shop: dict[str, Any], known_bad_hashes: list[int]) -> ModerationDecision:
    from platform_settings.flags import ai_moderation_enabled

    title, body = shop_text(shop)
    hit = keywords.check(title, body)
    if hit:
        return ModerationDecision(status=ModerationStatus.REJECTED, reason=f"banned keyword: '{hit}'", scores={"stage": "keywords"})

    reason, image_cache = await phash.check(shop_images(shop), known_bad_hashes)
    if reason:
        return ModerationDecision(status=ModerationStatus.REJECTED, reason=reason, scores={"stage": "phash"})
    for url, _h, data in image_cache:
        if data and (meta_reason := metadata.check(data, url=url)):
            return ModerationDecision(status=ModerationStatus.REJECTED, reason=meta_reason, scores={"stage": "metadata"})

    scores: dict[str, Any] = {}
    if config.enable_profanity_check:
        prof = profanity.score(title, body)
        if prof is not None:
            scores["profanity"] = prof
            if prof >= config.profanity_reject_threshold:
                return ModerationDecision(
                    status=ModerationStatus.REJECTED, reason=f"profanity score {prof:.2f}", scores=scores
                )

    if not ai_moderation_enabled():
        return ModerationDecision(status=ModerationStatus.APPROVED, reason="basic checks clean", scores=scores)

    text_result = await text_moderation.check(title, body)
    image_result = await image_moderation.check(image_cache) if image_cache else {"max_score": 0.0}
    scores.update(text=text_result, image=image_result)
    text_score, image_score = text_result.get("max_score"), image_result.get("max_score")
    if text_score is None or image_score is None:
        oai = await openai_moderation.check(title, body, shop_images(shop))
        scores["openai"] = oai
        if oai.get("flagged"):
            cats = ", ".join(oai.get("categories") or []) or "policy violation"
            return ModerationDecision(status=ModerationStatus.REJECTED, reason=f"flagged: {cats}", scores=scores)
        if oai.get("max_score") is not None:
            text_score = oai["max_score"] if text_score is None else text_score
            image_score = oai["max_score"] if image_score is None else image_score
    status, reason = _decide_from_scores(text_score, image_score)
    return ModerationDecision(status=status, reason=reason, scores=scores)


def _notify_owner(shop: dict[str, Any], live: bool) -> None:
    owner = shop.get("owner_id")
    if not owner:
        return
    try:
        from notifications.service import create_notification

        name = shop.get("name") or "Your shop"
        if live:
            create_notification(str(owner), f"{name} is live", "Your shop passed our checks and is now visible to buyers.",
                                metadata={"type": "shop_live", "shop_id": shop.get("id")})
        else:
            create_notification(str(owner), f"{name} is being reviewed",
                                "Something in your shop name, description or images needs a quick check by our team. "
                                "Edit the shop to fix it, or wait for a reviewer.",
                                metadata={"type": "shop_review", "shop_id": shop.get("id")})
    except Exception:  # noqa: BLE001
        logger.debug("shop moderation notification skipped", exc_info=True)


async def review_shop(shop_id: str) -> ModerationDecision | None:
    """Run the checks and activate the shop when clean. Never deactivates a live shop."""
    from db.supabase import get_supabase_admin

    from .service import load_bad_image_hashes

    admin = get_supabase_admin()
    rows = admin.table("shops").select(_SHOP_COLS).eq("id", shop_id).limit(1).execute().data or []
    if not rows:
        return None
    shop = rows[0]
    if shop.get("is_personal"):
        return None
    try:
        decision = await moderate_shop(shop, load_bad_image_hashes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("shop moderation failed for %s: %s", shop_id, exc)
        return None
    logger.info("shop moderation %s -> %s (%s)", shop_id, decision.status.value, decision.reason or "clean")
    if decision.status == ModerationStatus.APPROVED:
        if not shop.get("is_active"):
            from admin.service import set_shop_active

            set_shop_active(shop_id, True)
            _notify_owner(shop, live=True)
            try:
                from core.next_cache import revalidate_nextjs_cache_tag

                revalidate_nextjs_cache_tag("shops")
            except Exception:  # noqa: BLE001
                pass
    elif not shop.get("is_active"):
        _notify_owner(shop, live=False)
    return decision


_pending: set[Any] = set()


async def review_inactive_shops(limit: int = 25) -> dict[str, int]:
    """Check shops still waiting to go live (e.g. created before auto-activation existed)."""
    from db.supabase import get_supabase_admin

    rows = (
        get_supabase_admin().table("shops").select("id")
        .eq("is_active", False).eq("is_personal", False)
        .order("created_at", desc=True).limit(limit).execute().data or []
    )
    counts = {"checked": 0, "activated": 0}
    for row in rows:
        decision = await review_shop(str(row["id"]))
        counts["checked"] += 1
        if decision is not None and decision.status == ModerationStatus.APPROVED:
            counts["activated"] += 1
    return counts


async def review_shop_inline(shop_id: str, timeout_seconds: float = 20.0) -> ModerationDecision | None:
    """Wait briefly so the owner usually sees the shop live in the same response; finish in the background otherwise."""
    import asyncio

    task = asyncio.create_task(review_shop(shop_id))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.info("shop moderation for %s still running after %.0fs", shop_id, timeout_seconds)
        return None
