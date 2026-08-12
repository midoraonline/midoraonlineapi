"""Tier 3: local text profanity/toxicity via alt-profanity-check.

Zero network latency, CPU-only scikit-learn linear model (imports as
`profanity_check`). Runs before the Gemini/OpenAI calls so obvious garbage
is rejected without spending a model request or risking a 429.

The import is lazy and failure-tolerant: if the wheel isn't bundled on a
given deploy, `score()` returns None and the pipeline simply skips this
tier instead of erroring.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_predict_prob: Optional[Callable] = None
_import_failed = False


def _get_predictor() -> Optional[Callable]:
    global _predict_prob, _import_failed
    if _predict_prob is not None or _import_failed:
        return _predict_prob
    try:
        from profanity_check import predict_prob  # alt-profanity-check

        _predict_prob = predict_prob
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.info("alt-profanity-check unavailable; skipping profanity tier: %s", exc)
        _import_failed = True
    return _predict_prob


def score(title: str, description: str) -> Optional[float]:
    """Return profanity probability in [0, 1], or None if unavailable."""
    predictor = _get_predictor()
    if predictor is None:
        return None
    text = f"{title} {description or ''}".strip()
    if not text:
        return 0.0
    try:
        return float(predictor([text])[0])
    except Exception as exc:
        logger.warning("profanity scoring failed: %s", exc)
        return None
