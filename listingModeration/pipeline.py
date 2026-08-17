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
from uuid import UUID

from . import service
from .config import config
from .schemas import ModerationDecision, ModerationRow, ModerationStatus
from .stages import (
    image_moderation,
    keywords,
    openai_moderation,
    phash,
    profanity,
    text_moderation,
)


logger = logging.getLogger(__name__)


def _decide_from_scores(text_score: float | None, image_score: float | None) -> tuple[ModerationStatus, str | None]:
    # Unknown scores (Gemini failed / not configured). Fail-open when
    # explicitly allowed so listings don't get stuck at pending_review
    # forever on a transient outage or a self-hosted deploy without a
    # Gemini key. Keyword deny-list + pHash blocklist already ran clean at
    # this point, so this is not a full bypass — just a downgrade of the
    # model-only signal to "trust unless a cheap check hits".
    if text_score is None or image_score is None:
        if config.fail_open_when_model_unavailable:
            return (
                ModerationStatus.APPROVED,
                "auto-approved: model unavailable, cheap checks clean",
            )
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
    logger.info(
        "[Moderation] Starting moderation pipeline for row %s (product_id=%s, title='%s')",
        row.id,
        row.product_id or "N/A",
        row.title,
    )

    # Stage 1: keyword deny-list.
    hit = keywords.check(row.title, row.description)
    if hit:
        logger.warning(
            "[Moderation][Stage 1: Keywords] REJECTED row %s - Banned keyword matched: '%s'",
            row.id,
            hit,
        )
        return ModerationDecision(
            status=ModerationStatus.REJECTED,
            reason=f"banned keyword matched: '{hit}'",
            scores={"stage": "keywords", "match": hit},
        )
    logger.info("[Moderation][Stage 1: Keywords] PASSED for row %s", row.id)

    # Stage 2: pHash blocklist (also downloads image bytes we reuse in stage 4).
    phash_reason, image_cache = await phash.check(row.image_urls, known_bad_hashes)
    if phash_reason:
        logger.warning(
            "[Moderation][Stage 2: pHash] REJECTED row %s - Reason: %s",
            row.id,
            phash_reason,
        )
        return ModerationDecision(
            status=ModerationStatus.REJECTED,
            reason=phash_reason,
            scores={"stage": "phash"},
        )
    logger.info(
        "[Moderation][Stage 2: pHash] PASSED for row %s (%d image(s) downloaded/cached)",
        row.id,
        len(image_cache),
    )

    # Stage 3: local profanity classifier (CPU-only, no network call).
    if config.enable_profanity_check:
        prof = profanity.score(row.title, row.description)
        if prof is not None:
            scores["profanity"] = prof
            if prof >= config.profanity_reject_threshold:
                logger.warning(
                    "[Moderation][Stage 3: Profanity] REJECTED row %s - Score %.2f >= threshold %.2f",
                    row.id,
                    prof,
                    config.profanity_reject_threshold,
                )
                return ModerationDecision(
                    status=ModerationStatus.REJECTED,
                    reason=f"local profanity score {prof:.2f} >= reject threshold",
                    scores={"stage": "profanity", "profanity": prof},
                )
            logger.info(
                "[Moderation][Stage 3: Profanity] PASSED for row %s - Score: %.2f",
                row.id,
                prof,
            )

    # Stage 4: text moderation via Gemini.
    text_result = await text_moderation.check(row.title, row.description)
    scores["text"] = text_result
    logger.info(
        "[Moderation][Stage 4: Gemini Text] Row %s result: max_score=%s",
        row.id,
        text_result.get("max_score"),
    )

    # Stage 5: image moderation via Gemini vision (reuse downloaded bytes).
    image_result = await image_moderation.check(image_cache)
    scores["image"] = image_result
    logger.info(
        "[Moderation][Stage 5: Gemini Vision] Row %s result: max_score=%s",
        row.id,
        image_result.get("max_score"),
    )

    text_score = text_result.get("max_score")
    image_score = image_result.get("max_score")

    # Stage 6: free multimodal failover. When Gemini couldn't score (no key,
    # 429, or bad JSON) fall back to OpenAI's free omni-moderation endpoint
    # so the listing gets a real decision instead of parking in review.
    if text_score is None or image_score is None:
        logger.info(
            "[Moderation][Stage 6: OpenAI Failover] Triggered for row %s (Gemini scores: text=%s, image=%s)",
            row.id,
            text_score,
            image_score,
        )
        oai = await openai_moderation.check(row.title, row.description, row.image_urls)
        scores["openai"] = oai
        if oai.get("flagged"):
            cats = ", ".join(oai.get("categories") or []) or "policy violation"
            logger.warning(
                "[Moderation][Stage 6: OpenAI Failover] REJECTED row %s - Flagged categories: %s",
                row.id,
                cats,
            )
            return ModerationDecision(
                status=ModerationStatus.REJECTED,
                reason=f"openai moderation flagged: {cats}",
                scores=scores,
            )
        oai_score = oai.get("max_score")
        if oai_score is not None:
            # Backfill whichever Gemini signal is missing with the OpenAI score.
            if text_score is None:
                text_score = oai_score
            if image_score is None:
                image_score = oai_score
            logger.info(
                "[Moderation][Stage 6: OpenAI Failover] PASSED for row %s - Max score: %.2f",
                row.id,
                oai_score,
            )

    status, reason = _decide_from_scores(text_score, image_score)
    logger.info(
        "[Moderation] FINAL DECISION for row %s (product_id=%s) -> Status: %s | Reason: '%s' | Scores: %s",
        row.id,
        row.product_id or "N/A",
        status.value.upper(),
        reason or "Clean",
        scores,
    )
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

    logger.info("[Moderation] Processing batch of %d row(s) (reclaimed=%d)", len(rows), reclaimed)

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
            from . import notifications
            await notifications.notify_decision(row, decision)
            counts[decision.status.value] = counts.get(decision.status.value, 0) + 1
        except Exception as exc:
            logger.exception("[Moderation] Batch moderation failed for row %s", row.id)
            service.mark_failed(row.id, repr(exc))
            counts["failed"] += 1

    logger.info("[Moderation] Batch process complete: %s", counts)
    return counts


_RECONCILE_TERMINAL = {"approved", "rejected", "needs_review"}
_RECONCILE_IN_FLIGHT = {"pending", "processing"}


def reconcile(limit: int) -> dict[str, int]:
    """Heal products stuck in `pending_review`.

    For each pending_review product:
      * a queue row already carrying a decision  -> re-sync products.status
        (covers "review finished but status never synced");
      * a queue row still pending/processing     -> leave it for the drain;
      * no row (or only terminal-failed rows)     -> enqueue a fresh row.

    Returns per-outcome counts. Does not drain — the caller runs the batch.
    """
    products = service.fetch_products_pending_review(limit)
    counts = {"scanned": len(products), "resynced": 0, "enqueued": 0, "in_flight": 0}
    if not products:
        return counts

    rows_by_product = service.load_queue_rows_for_products(
        [str(p["id"]) for p in products]
    )

    from .hooks import enqueue_product

    for product in products:
        pid = str(product["id"])
        rows = rows_by_product.get(pid, [])
        decided = next(
            (r for r in rows if r.get("status") in _RECONCILE_TERMINAL), None
        )
        in_flight = any(r.get("status") in _RECONCILE_IN_FLIGHT for r in rows)

        if decided:
            decision = ModerationDecision(
                status=ModerationStatus(decided["status"]),
                reason=decided.get("reason"),
                scores=decided.get("scores") or {},
            )
            service.sync_product_status(pid, decision)
            counts["resynced"] += 1
        elif in_flight:
            counts["in_flight"] += 1
        elif enqueue_product(product) is not None:
            counts["enqueued"] += 1

    logger.info("[Moderation] Reconcile complete: %s", counts)
    return counts


async def process_row(row_id: UUID | str) -> ModerationDecision | None:
    """Run the pipeline synchronously on a single queue row.

    Used by the inline-on-enqueue hook so a listing gets its final status
    without waiting for the next cron tick. Returns None when the row is
    missing or already claimed by a concurrent drain.
    """
    logger.info("[Moderation] Executing inline moderation for row %s", row_id)
    row = service.claim_by_id(row_id)
    if row is None:
        logger.warning("[Moderation] Inline moderation skipped: row %s not found or already claimed", row_id)
        return None

    known_bad_hashes = service.load_bad_image_hashes()
    try:
        decision = await moderate(row, known_bad_hashes)
    except Exception as exc:
        logger.exception("[Moderation] Inline moderation failed for row %s", row.id)
        service.mark_failed(row.id, repr(exc))
        return None

    service.write_decision(row.id, decision)
    if row.product_id:
        service.sync_product_status(row.product_id, decision)
    from . import notifications
    await notifications.notify_decision(row, decision)
    logger.info("[Moderation] Inline moderation finished for row %s -> %s", row.id, decision.status.value.upper())
    return decision

