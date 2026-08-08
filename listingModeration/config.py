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
