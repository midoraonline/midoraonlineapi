"""Parse UploadThing file keys out of stored media URLs.

Keys live in the path as `/f/<key>` on `*.ufs.sh` and `utfs.io`.
Videos are not a separate column; they sit in `image_urls` and are
detected the same way as the publish gates.
"""
from __future__ import annotations

from urllib.parse import urlparse

from shop.publish_gates import is_video_url

_UPLOAD_HOSTS = ("ufs.sh", "utfs.io")


def _host_is_uploadthing(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in _UPLOAD_HOSTS)


def file_key_from_url(url: str | None) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not _host_is_uploadthing(parsed.hostname or ""):
        return None
    path = parsed.path or ""
    marker = "/f/"
    idx = path.find(marker)
    if idx < 0:
        return None
    key = path[idx + len(marker):].split("/", 1)[0].strip()
    return key or None


def normalize_urls(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    return []


def keys_from_urls(urls) -> tuple[list[str], list[str]]:
    """Return `(image_keys, video_keys)`.

    `image_keys` is every UploadThing key, including videos.
    `video_keys` is the video subset. Order follows the URL list.
    """
    image_keys: list[str] = []
    video_keys: list[str] = []
    seen_images: set[str] = set()
    seen_videos: set[str] = set()
    for url in normalize_urls(urls):
        key = file_key_from_url(url)
        if not key:
            continue
        if key not in seen_images:
            seen_images.add(key)
            image_keys.append(key)
        if is_video_url(url) and key not in seen_videos:
            seen_videos.add(key)
            video_keys.append(key)
    return image_keys, video_keys


def _clean_client_keys(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def resolve_stored_keys(urls, client_image_keys=None, client_video_keys=None) -> tuple[list[str], list[str]]:
    """Prefer keys parsed from URLs. An empty URL list clears stored keys."""
    normalized = normalize_urls(urls)
    if not normalized:
        return [], []
    image_keys, video_keys = keys_from_urls(normalized)
    if image_keys:
        return image_keys, video_keys
    return _clean_client_keys(client_image_keys), _clean_client_keys(client_video_keys)


def removed_file_keys(old_urls, new_urls) -> list[str]:
    old_keys, _old_videos = keys_from_urls(old_urls)
    new_keys, _new_videos = keys_from_urls(new_urls)
    kept = set(new_keys)
    removed: list[str] = []
    seen: set[str] = set()
    for key in old_keys:
        if key in kept or key in seen:
            continue
        seen.add(key)
        removed.append(key)
    return removed
