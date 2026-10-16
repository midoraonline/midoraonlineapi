"""Server-side background removal for listing photos.

Uses remove.bg at full resolution (size=full, PNG) so quality is never reduced,
then stores the result on UploadThing and registers it to the user.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from core.config import get_settings

logger = logging.getLogger(__name__)

REMOVE_BG_URL = "https://api.remove.bg/v1.0/removebg"
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


class BackgroundRemovalUnavailable(RuntimeError):
    pass


class BackgroundRemovalFailed(RuntimeError):
    pass


def _valid_source(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def normalize_color(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not _HEX.match(raw):
        return None
    return raw.lstrip("#").lower()


async def remove_background_to_url(image_url: str, *, user_id: str, bg_color: str | None = None) -> str:
    api_key = (getattr(get_settings(), "remove_bg_api_key", "") or "").strip()
    if not api_key:
        raise BackgroundRemovalUnavailable("Background removal isn't set up on the server yet.")
    if not _valid_source(image_url):
        raise BackgroundRemovalFailed("Use an uploaded photo URL.")

    data = {"image_url": image_url, "size": "full", "format": "png"}
    color = normalize_color(bg_color)
    if color:
        data["bg_color"] = color
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(REMOVE_BG_URL, data=data, headers={"X-Api-Key": api_key})
    if resp.status_code >= 400:
        logger.warning("remove.bg returned %s: %s", resp.status_code, resp.text[:300])
        raise BackgroundRemovalFailed("Couldn't remove the background from this photo.")

    from media.uploadthing import upload_file_bytes

    stem = (urlparse(image_url).path.rsplit("/", 1)[-1] or "photo").split(".")[0][:40] or "photo"
    try:
        url = await upload_file_bytes(resp.content, filename=f"{stem}-nobg.png", content_type="image/png")
    except RuntimeError as exc:
        raise BackgroundRemovalFailed("Couldn't save the edited photo.") from exc

    try:
        from db.supabase import get_supabase_admin
        from media.registry import register_upload

        key = url.rstrip("/").rsplit("/", 1)[-1]
        register_upload(get_supabase_admin(), key=key, url=url, user_id=user_id, kind="listingPhoto")
    except Exception as exc:  # noqa: BLE001 - registry is best effort
        logger.warning("register bg-removed upload failed: %s", exc)
    return url
