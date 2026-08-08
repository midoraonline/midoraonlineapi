"""Moderation pipeline — cheapest checks first, short-circuit on any hit.

Order:
    1. Banned-keyword check on title/description        (microseconds)
    2. Perceptual-hash blocklist against known-bad set   (network I/O)
    3. Gemini text moderation                            (~1-3s)
    4. Gemini image moderation                           (~1-3s per image)
    5. Threshold-based decision -> approved | rejected | needs_review

Runs synchronously inside a Vercel Function invocation (called by the drain
endpoint). Do NOT try to fire this off with `BackgroundTasks` on Vercel —
the function process is killed as soon as the response ships.
"""
from __future__ import annotations

import logging
from typing import Any

from . import service
from .config import config
from .schemas import ModerationDecision, ModerationRow, ModerationStatus
from .stages import image_moderation, keywords, phash, text_moderation

logger = logging.getLogger(__name__)


def _decide_from_scores(text_score: float | None, image_score: float | None) -> tuple[ModerationStatus, str | None]:
    # Unknown scores (Gemini failed / not configured) push to needs_review
    # rather than falsely approving. Never auto-reject on unknowns.
    if text_score is None or image_score is None:
        return ModerationStatus.NEEDS_REVIEW, "model unavailable — manual review required"

    if text_score >= config.text_reject_threshold:
        return ModerationStatus.REJECTED, f"text score {text_score:.2f} >= reject threshold"
    if image_score >= config.image_reject_threshold:
        return ModerationStatus.REJECTED, f"image score {image_score:.2f} >= reject threshold"

    if text_score >= config.text_review_threshold:
        return ModerationStatus.NEEDS_REVIEW, f"text score {text_score:.2f} in ambiguous range"
    if image_score >= config.image_review_threshold:
        return ModerationStatus.NEEDS_REVIEW, f"image score {image_score:.2f} in ambiguous range"

    return ModerationStatus.APPROVED, None


async def moderate(row: ModerationRow, known_bad_hashes: list[int]) -> ModerationDecision:
    scores: dict[str, Any] = {}

    # Stage 1: keyword deny-list.
    hit = keywords.check(row.title, row.description)
    if hit:
        return ModerationDecision(
            status=ModerationStatus.REJECTED,
            reason=f"banned keyword matched: '{hit}'",
            scores={"stage": "keywords", "match": hit},
        )

    # Stage 2: pHash blocklist (also downloads image bytes we reuse in stage 4).
    phash_reason, image_cache = await phash.check(row.image_urls, known_bad_hashes)
    if phash_reason:
        return ModerationDecision(
            status=ModerationStatus.REJECTED,
            reason=phash_reason,
            scores={"stage": "phash"},
        )

    # Stage 3: text moderation via Gemini.
    text_result = await text_moderation.check(row.title, row.description)
    scores["text"] = text_result

    # Stage 4: image moderation via Gemini vision (reuse downloaded bytes).
    image_result = await image_moderation.check(image_cache)
    scores["image"] = image_result

    text_score = text_result.get("max_score")
    image_score = image_result.get("max_score")

    status, reason = _decide_from_scores(text_score, image_score)
    return ModerationDecision(status=status, reason=reason, scores=scores)


async def process_batch(batch_size: int) -> dict[str, int]:
    """Drain up to `batch_size` pending rows. Returns per-status counts."""
    # Recover rows stuck in 'processing' from prior invocations that timed out.
    reclaimed = service.reclaim_stuck(config.stuck_after_seconds)

    rows = service.claim_batch(batch_size)
    counts = {
        "reclaimed": reclaimed,
        "processed": 0,
        "approved": 0,
        "rejected": 0,
        "needs_review": 0,
        "failed": 0,
    }
    if not rows:
        return counts

    # One DB round-trip for the whole batch — the set is expected to be small
    # (hundreds of entries at most).
    known_bad_hashes = service.load_bad_image_hashes()

    for row in rows:
        counts["processed"] += 1
        try:
            decision = await moderate(row, known_bad_hashes)
            service.write_decision(row.id, decision)
            if row.product_id:
                service.sync_product_status(row.product_id, decision)
            counts[decision.status.value] = counts.get(decision.status.value, 0) + 1
        except Exception as exc:
            logger.exception("moderation failed for row %s", row.id)
            service.mark_failed(row.id, repr(exc))
            counts["failed"] += 1

    return counts
