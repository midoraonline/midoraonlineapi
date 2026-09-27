"""Delete files through the UploadThing REST API.

The Next.js app uses `UPLOADTHING_TOKEN` (dashboard token, sometimes a
base64 JSON blob with `apiKey`). Older setups use `UPLOADTHING_SECRET`
as the raw API key. Either value is sent as `x-uploadthing-api-key`.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

DELETE_URL = "https://api.uploadthing.com/v6/deleteFiles"
UPLOAD_URL = "https://api.uploadthing.com/v6/uploadFiles"


def decode_uploadthing_key(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("sk_"):
        return text
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = text + "=" * (-len(text) % 4)
            payload = json.loads(decoder(padded))
        except Exception:
            continue
        if isinstance(payload, dict):
            api_key = payload.get("apiKey") or payload.get("api_key")
            if api_key:
                return str(api_key).strip()
    return text


def uploadthing_api_key() -> str | None:
    raw = (os.getenv("UPLOADTHING_TOKEN") or os.getenv("UPLOADTHING_SECRET") or "").strip()
    if not raw:
        from core.config import get_settings

        settings = get_settings()
        raw = (settings.uploadthing_token or settings.uploadthing_secret or "").strip()
    if not raw:
        logger.warning(
            "UPLOADTHING_TOKEN is not set (UPLOADTHING_SECRET also accepted); skipping UploadThing delete"
        )
        return None
    key = decode_uploadthing_key(raw)
    if not key:
        logger.warning("UPLOADTHING_TOKEN is set but no API key could be read; skipping delete")
        return None
    return key


def _uploaded_url(payload: object) -> str | None:
    rows: list = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        data = payload.get("data") or payload.get("files") or payload.get("file")
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = [data]
        elif payload.get("url"):
            rows = [payload]
    for row in rows:
        if isinstance(row, dict):
            url = row.get("url") or row.get("ufsUrl") or row.get("appUrl")
            if url:
                return str(url)
    return None


async def upload_file_bytes(data: bytes, *, filename: str, content_type: str) -> str:
    """Upload original bytes. Returns the file URL. Does not transcode."""
    api_key = uploadthing_api_key()
    if not api_key:
        raise RuntimeError("uploadthing_unconfigured")
    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            UPLOAD_URL,
            headers={"x-uploadthing-api-key": api_key},
            files={"files": (filename, data, content_type)},
        )
    if response.status_code >= 400:
        logger.warning(
            "UploadThing uploadFiles returned %s: %s",
            response.status_code,
            response.text[:300],
        )
        raise RuntimeError("upload_failed")
    try:
        url = _uploaded_url(response.json())
    except Exception as exc:
        raise RuntimeError("upload_failed") from exc
    if not url:
        raise RuntimeError("upload_failed")
    return url


async def delete_file_keys(keys: list[str]) -> None:
    api_key = uploadthing_api_key()
    if not api_key or not keys:
        return
    unique = list(dict.fromkeys(key for key in keys if key))
    if not unique:
        return
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for start in range(0, len(unique), 100):
            chunk = unique[start:start + 100]
            try:
                response = await client.post(
                    DELETE_URL,
                    headers={
                        "x-uploadthing-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"fileKeys": chunk},
                )
            except httpx.HTTPError as exc:
                logger.warning("UploadThing deleteFiles request failed: %s", exc)
                continue
            if response.status_code >= 400:
                logger.warning(
                    "UploadThing deleteFiles returned %s: %s",
                    response.status_code,
                    response.text[:300],
                )
