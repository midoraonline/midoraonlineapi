"""Stage 4: image nudity / policy check via Gemini vision.

Replaces NudeNet (won't fit in a Vercel Python bundle). Sends already-
downloaded image bytes to Gemini and asks for structured scores.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from core.config import get_settings

from ..config import config
from ._retry import call_with_retry

logger = logging.getLogger(__name__)

_PROMPT = """You are a strict e-commerce image content moderator. Rate this
image against each policy category. Return ONLY a JSON object, no prose,
no code fences.

Categories (each 0.0 to 1.0, where 1.0 = certain violation):
  nudity         - exposed genitals, sexual acts, explicit content
  suggestive     - lingerie, swimwear positioned suggestively, near-nudity
  violence       - graphic gore, weapons brandished, corpses
  weapons_sale   - firearms, ammunition being sold
  drugs          - illicit drugs, drug paraphernalia
  hate_symbols   - hate group iconography

Also return "verdict": one of "clean", "borderline", "violation".

Respond with JSON only:
{"nudity": 0.0, "suggestive": 0.0, "violence": 0.0, "weapons_sale": 0.0,
 "drugs": 0.0, "hate_symbols": 0.0, "verdict": "clean"}
"""


def _guess_mime(url: str, data: bytes) -> str:
    lower = url.lower()
    if lower.endswith(".png") or data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if lower.endswith(".webp") or data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if lower.endswith(".gif") or data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def _score_one(client: Any, model: str, url: str, data: bytes) -> Optional[dict[str, Any]]:
    try:
        from google.genai import types  # type: ignore

        resp = await call_with_retry(
            lambda: client.aio.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=data, mime_type=_guess_mime(url, data)),
                    _PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            ),
            max_retries=config.gemini_max_retries,
            base_delay=config.gemini_retry_base_delay,
            label="gemini image moderation",
        )
    except Exception as exc:
        logger.warning("gemini image moderation call failed for %s: %s", url, exc)
        return None

    text = getattr(resp, "text", "") or ""
    try:
        parsed = _parse_json_object(text)
    except Exception as exc:
        logger.warning("gemini image moderation: bad JSON (%s): %r", exc, text[:200])
        return None

    sub_scores = {
        k: float(v)
        for k, v in parsed.items()
        if isinstance(v, (int, float)) and k != "verdict"
    }
    return {
        "url": url,
        "sub_scores": sub_scores,
        "verdict": str(parsed.get("verdict", "unknown")),
        "max_score": max(sub_scores.values()) if sub_scores else 0.0,
    }


async def check(
    downloaded_images: list[tuple[str, Optional[int], Optional[bytes]]],
) -> dict[str, Any]:
    """Score each successfully-downloaded image and return the worst.

    `downloaded_images` comes from the phash stage — reusing bytes avoids a
    second network round-trip per image.
    """
    settings = get_settings()
    usable = [(url, data) for url, _phash, data in downloaded_images if data]
    if not usable:
        return {"max_score": 0.0, "per_image": [], "verdict": "clean"}

    if not settings.gemini_api_key:
        logger.info("gemini not configured; skipping image moderation")
        return {"max_score": None, "per_image": [], "verdict": "unknown"}

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        logger.warning("google-genai import failed: %s", exc)
        return {"max_score": None, "per_image": [], "verdict": "unknown"}

    client = genai.Client(api_key=settings.gemini_api_key)

    per_image: list[dict[str, Any]] = []
    worst = 0.0
    worst_verdict = "clean"

    # Sequential, not parallel: Gemini free-tier rate limits punish burst
    # traffic. If you're on a paid tier, wrap these in asyncio.gather().
    for url, data in usable:
        result = await _score_one(client, settings.gemini_model, url, data)
        if result is None:
            per_image.append({"url": url, "error": "gemini_failed"})
            continue
        per_image.append(result)
        if result["max_score"] > worst:
            worst = result["max_score"]
            worst_verdict = result["verdict"]

    return {"max_score": worst, "per_image": per_image, "verdict": worst_verdict}
