"""Policy knobs for listing moderation.

Everything auto-tuneable at deploy time is an env var so we don't need a
redeploy to loosen a threshold that's rejecting legitimate listings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModerationConfig:
    # Batch size per drain invocation. Keep small so a single Vercel function
    # invocation finishes well within the 60s cap (Gemini calls dominate).
    batch_size: int = _env_int("MODERATION_BATCH_SIZE", 5)

    # A processing row older than this is considered stuck (function died
    # mid-run) and is reset to pending at the top of the next drain pass.
    stuck_after_seconds: int = _env_int("MODERATION_STUCK_AFTER_SECONDS", 300)

    # Text moderation thresholds. Gemini scores are 0..1.
    text_reject_threshold: float = _env_float("MODERATION_TEXT_REJECT", 0.85)
    text_review_threshold: float = _env_float("MODERATION_TEXT_REVIEW", 0.50)

    # Image moderation thresholds. Same 0..1 scale.
    image_reject_threshold: float = _env_float("MODERATION_IMAGE_REJECT", 0.90)
    image_review_threshold: float = _env_float("MODERATION_IMAGE_REVIEW", 0.60)

    # aHash Hamming distance <= this counts as a match against the blocklist.
    # Higher = catches more edits, but also more false positives.
    phash_distance_threshold: int = _env_int("MODERATION_PHASH_DISTANCE", 5)

    # Max images to fetch/moderate per listing. Prevents a single row with 30
    # images from eating the whole drain budget. Matches UploadThing's
    # productImage.maxFileCount (see midora/app/api/uploadthing/core.ts).
    max_images_per_listing: int = _env_int("MODERATION_MAX_IMAGES", 8)

    # Per-image HTTP timeout when downloading for pHash / Gemini.
    image_download_timeout_seconds: float = _env_float("MODERATION_IMAGE_DOWNLOAD_TIMEOUT", 8.0)

    # Max bytes to read per image (guards against huge images). 5 MB.
    max_image_bytes: int = _env_int("MODERATION_MAX_IMAGE_BYTES", 5 * 1024 * 1024)

    # Run the pipeline synchronously right after enqueue instead of waiting
    # for the cron drain. Ensures listings complete even when the cron isn't
    # firing (Vercel Hobby caps at once-per-day; CRON_SECRET missing in prod
    # returns 503). The cron becomes a safety net for reclaim/retry only.
    inline_on_enqueue: bool = _env_bool("MODERATION_INLINE_ON_ENQUEUE", True)

    # Max wall-clock we're willing to spend on the inline pipeline run before
    # we bail and let the cron pick it up. Guards create_product latency.
    inline_timeout_seconds: float = _env_float("MODERATION_INLINE_TIMEOUT", 25.0)

    # When Gemini is unavailable (no key, transient failure, bad JSON) and
    # cheap checks (keywords + pHash) came back clean, default to APPROVED
    # instead of parking in needs_review forever. Toggle off if you want a
    # human eye on everything when the model can't score. Recommended ON in
    # production so listings don't get stuck on a transient outage.
    fail_open_when_model_unavailable: bool = _env_bool(
        "MODERATION_FAIL_OPEN_WHEN_MODEL_UNAVAILABLE",
        True,
    )


# Deny-list is intentionally short here; extend from your real policy doc.
# Kept in code (not the DB) because these are policy, not data — they should
# be reviewed in PRs, not by SQL edits.
BANNED_KEYWORDS: frozenset[str] = frozenset({
    "counterfeit",
    "replica",
    "fake rolex",
    "stolen",
    "child porn",
    "cp for sale",
    "human trafficking",
    "escort service",
    "meth",
    "cocaine for sale",
    "gun for sale",
    "ak-47 for sale",
})


config = ModerationConfig()
