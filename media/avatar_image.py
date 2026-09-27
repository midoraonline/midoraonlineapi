"""Avatar bytes for UploadThing.

JPEG, PNG, WebP, and GIF are uploaded unchanged. HEIC/HEIF is converted to
JPEG at quality 100 and the original pixel size. Nothing is resized.
"""

from __future__ import annotations

import io

from PIL import Image

MAX_AVATAR_BYTES = 20 * 1024 * 1024
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"}
_PASSTHROUGH = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}


class AvatarImageError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def is_heic(data: bytes, content_type: str | None, filename: str | None) -> bool:
    kind = _content_type(content_type)
    if "heic" in kind or "heif" in kind:
        return True
    name = (filename or "").lower()
    if name.endswith(".heic") or name.endswith(".heif"):
        return True
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS


def heic_to_jpeg(data: bytes) -> bytes:
    """Full-resolution JPEG. Raises AvatarImageError when the converter is missing."""
    try:
        from pillow_heif import open_heif
    except Exception as exc:
        raise AvatarImageError(
            "HEIC conversion is not available on this server.",
            "heic_converter_unavailable",
        ) from exc
    try:
        image = open_heif(data).to_pillow()
    except Exception as exc:
        raise AvatarImageError("Could not read that HEIC photo.", "avatar_unreadable") from exc
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=100, subsampling=0)
    return out.getvalue()


def prepare_avatar_upload(
    data: bytes,
    *,
    content_type: str | None,
    filename: str | None,
) -> tuple[bytes, str, str]:
    """Return bytes, content type, and filename to store. Non-HEIC bytes are copied as-is."""
    if not data:
        raise AvatarImageError("Choose a photo to upload.", "avatar_empty")
    if len(data) > MAX_AVATAR_BYTES:
        raise AvatarImageError("Profile photos must be 20 MB or smaller.", "avatar_too_large")
    name = (filename or "avatar").strip() or "avatar"
    if is_heic(data, content_type, name):
        jpeg = heic_to_jpeg(data)
        stem = name.rsplit(".", 1)[0] or "avatar"
        return jpeg, "image/jpeg", f"{stem}.jpg"
    kind = _content_type(content_type)
    if kind not in _PASSTHROUGH:
        # Some browsers omit the type. Sniff a still image and keep the original bytes.
        try:
            with Image.open(io.BytesIO(data)) as image:
                detected = (image.format or "").upper()
        except Exception as exc:
            raise AvatarImageError(
                "Upload a JPEG, PNG, WebP, GIF, or HEIC photo.",
                "avatar_type",
            ) from exc
        mapped = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
        if detected not in mapped:
            raise AvatarImageError(
                "Upload a JPEG, PNG, WebP, GIF, or HEIC photo.",
                "avatar_type",
            )
        kind = mapped[detected]
    return bytes(data), kind, name
