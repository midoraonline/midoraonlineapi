"""Tier 4: free multimodal moderation failover via OpenAI omni-moderation.

Used when Gemini is unavailable or rate-limited (429). The
`/v1/moderations` endpoint is not billed and accepts text + image URLs in a
single request, which fits Vercel's short function budget. UploadThing URLs
are sent as-is (no byte download needed).

Any failure returns max_score=None so the pipeline treats a missing OpenAI
signal the same 'unknown' way it treats a Gemini outage (fail-open logic in
pipeline._decide_from_scores still applies).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import get_settings

from ..config import config
from .phash import is_video_url

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.openai.com/v1/moderations"


async def check(title: str, description: str, image_urls: list[str]) -> dict[str, Any]:
    """Return {'max_score', 'flagged', 'categories'}; max_score=None on failure."""
    settings = get_settings()
    api_key = getattr(settings, "openai_api_key", "") or ""
    if not api_key or not config.enable_openai_free_api:
        return {"max_score": None, "flagged": False, "categories": []}

    text = f"{title}\n{description or ''}".strip()
    input_payload: list[dict[str, Any]] = []
    if text:
        input_payload.append({"type": "text", "text": text[:10_000]})
    for url in image_urls[: config.max_images_per_listing]:
        if url and not is_video_url(url):
            input_payload.append({"type": "image_url", "image_url": {"url": url}})

    if not input_payload:
        return {"max_score": 0.0, "flagged": False, "categories": []}

    try:
        timeout = config.image_download_timeout_seconds + 4.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": config.openai_moderation_model, "input": input_payload},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("openai moderation call failed: %s", exc)
        return {"max_score": None, "flagged": False, "categories": []}

    results = data.get("results") or []
    if not results:
        return {"max_score": None, "flagged": False, "categories": []}

    flagged = False
    max_score = 0.0
    flagged_categories: set[str] = set()
    for result in results:
        if result.get("flagged"):
            flagged = True
        for cat, on in (result.get("categories") or {}).items():
            if on:
                flagged_categories.add(cat)
        for _cat, sc in (result.get("category_scores") or {}).items():
            try:
                max_score = max(max_score, float(sc))
            except (TypeError, ValueError):
                continue

    return {
        "max_score": max_score,
        "flagged": flagged,
        "categories": sorted(flagged_categories),
    }
