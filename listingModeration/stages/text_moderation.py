"""Stage 3: text toxicity / policy check via Gemini.

Replaces Detoxify (which won't fit in a Vercel Python bundle). We ask
Gemini for a structured JSON response with sub-scores and take the max —
same shape the pipeline expects.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.config import get_settings

from ..config import config
from ._retry import call_with_retry

logger = logging.getLogger(__name__)

_PROMPT = """You are a strict e-commerce content moderator. Score the following
LISTING TITLE + DESCRIPTION against each policy category. Return ONLY a JSON
object, no prose, no code fences.

Categories (each 0.0 to 1.0, where 1.0 = certain violation):
  toxicity       - insults, harassment, hate speech
  sexual         - sexually explicit language or solicitation
  violence       - graphic violence, threats, weapons trafficking
  illegal_goods  - drugs, stolen goods, counterfeit, weapons for sale
  scam           - phishing, wire-fraud patterns, impossible-price bait
  personal_info  - phone/id/bank details being sold or doxxed

Also return "verdict": one of "clean", "borderline", "violation".

TITLE:
{title}

DESCRIPTION:
{description}

Respond with JSON only:
{{"toxicity": 0.0, "sexual": 0.0, "violence": 0.0, "illegal_goods": 0.0,
  "scam": 0.0, "personal_info": 0.0, "verdict": "clean"}}
"""


def _parse_json_object(text: str) -> dict[str, Any]:
    """Gemini sometimes wraps JSON in ```json ... ``` fences; strip them."""
    text = text.strip()
    if text.startswith("```"):
        # Drop opening fence line and closing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def check(title: str, description: str) -> dict[str, Any]:
    """Return {'max_score': float, 'sub_scores': dict, 'verdict': str}.

    On any Gemini error we return max_score=None so the pipeline can push the
    row into needs_review rather than falsely approving it.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.info("gemini not configured; skipping text moderation")
        return {"max_score": None, "sub_scores": {}, "verdict": "unknown"}

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:
        logger.warning("google-genai import failed: %s", exc)
        return {"max_score": None, "sub_scores": {}, "verdict": "unknown"}

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = _PROMPT.format(title=title[:2000], description=(description or "")[:8000])

    try:
        resp = await call_with_retry(
            lambda: client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            ),
            max_retries=config.gemini_max_retries,
            base_delay=config.gemini_retry_base_delay,
            label="gemini text moderation",
        )
    except Exception as exc:
        logger.warning("gemini text moderation call failed: %s", exc)
        return {"max_score": None, "sub_scores": {}, "verdict": "unknown"}

    text = getattr(resp, "text", "") or ""
    try:
        data = _parse_json_object(text)
    except Exception as exc:
        logger.warning("gemini text moderation: bad JSON (%s): %r", exc, text[:200])
        return {"max_score": None, "sub_scores": {}, "verdict": "unknown"}

    sub_scores = {
        k: float(v)
        for k, v in data.items()
        if isinstance(v, (int, float)) and k != "verdict"
    }
    verdict = str(data.get("verdict", "unknown"))
    max_score = max(sub_scores.values()) if sub_scores else 0.0
    return {"max_score": max_score, "sub_scores": sub_scores, "verdict": verdict}
