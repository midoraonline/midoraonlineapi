"""Stage 2: perceptual-hash blocklist.

Uses a tiny Pillow-only aHash (average hash) — no numpy dependency, keeps
the Vercel bundle small. Hamming distance against a set of known-bad
hashes loaded from Supabase. Catches re-uploads of previously-rejected
images including light edits (crop, resize, slight recolor).
"""
from __future__ import annotations

import io
import logging
import re
from typing import Optional

import httpx

from ..config import config

logger = logging.getLogger(__name__)

# Matches the frontend's `isVideoUrl` in midora/lib/api/products.ts:
# UploadThing URLs often lack extensions, so productcard tags them with a
# fragment or query param. Skip these here — pHash on a video URL yields
# garbage and Gemini image scoring below can't consume mp4 bytes.
_VIDEO_EXT_RE = re.compile(r"\.(mp4|webm|mov|m4v)(?:$|\?|#)", re.IGNORECASE)


def is_video_url(url: str) -> bool:
    if not url:
        return False
    if "#midora-video" in url.lower():
        return True
    if "midora_media=video" in url.lower():
        return True
    return bool(_VIDEO_EXT_RE.search(url))


def _ahash(image_bytes: bytes) -> Optional[int]:
    """Return a 64-bit average-hash for the image, or None if it can't be decoded."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency in requirements
        logger.error("Pillow not installed; skipping phash stage")
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            small = im.convert("L").resize((8, 8), Image.Resampling.BILINEAR)
            pixels = list(small.getdata())
    except Exception as exc:
        logger.debug("phash: unable to decode image: %s", exc)
        return None

    avg = sum(pixels) / 64.0
    bits = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            bits |= 1 << i
    # Fit into signed 64-bit for Postgres BIGINT round-trip.
    if bits >= 1 << 63:
        bits -= 1 << 64
    return bits


def _hamming(a: int, b: int) -> int:
    # XOR unsigned for a stable bit count.
    ua = a & 0xFFFFFFFFFFFFFFFF
    ub = b & 0xFFFFFFFFFFFFFFFF
    return bin(ua ^ ub).count("1")


async def _download(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    try:
        resp = await client.get(url, timeout=config.image_download_timeout_seconds)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("phash: download failed for %s: %s", url, exc)
        return None
    data = resp.content
    if len(data) > config.max_image_bytes:
        return None
    return data


async def check(
    image_urls: list[str],
    known_bad_hashes: list[int],
) -> tuple[Optional[str], list[tuple[str, Optional[int], Optional[bytes]]]]:
    """Return (reject_reason_or_None, per_image_cache).

    `per_image_cache` is a list of (url, phash, raw_bytes) tuples so downstream
    stages (Gemini image moderation) can reuse the already-downloaded bytes
    instead of re-fetching. `raw_bytes` may be None when download failed.
    """
    cache: list[tuple[str, Optional[int], Optional[bytes]]] = []
    if not image_urls:
        return None, cache

    # Videos live in the same array on the products table; skip them here.
    still_images = [u for u in image_urls if not is_video_url(u)]
    urls = still_images[: config.max_images_per_listing]

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for url in urls:
            data = await _download(client, url)
            if data is None:
                cache.append((url, None, None))
                continue

            h = _ahash(data)
            cache.append((url, h, data))

            if h is None:
                continue
            for bad in known_bad_hashes:
                if _hamming(h, bad) <= config.phash_distance_threshold:
                    return f"image matches known-bad hash (url={url})", cache

    return None, cache
