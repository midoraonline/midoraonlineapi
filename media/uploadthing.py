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


PREPARE_URL = "https://api.uploadthing.com/v7/prepareUpload"


def uploadthing_app_id() -> str | None:
    raw = (os.getenv("UPLOADTHING_TOKEN") or "").strip()
    if not raw:
        try:
            from core.config import get_settings

            raw = (get_settings().uploadthing_token or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            payload = json.loads(decoder(raw + "=" * (-len(raw) % 4)))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("appId"):
            return str(payload["appId"])
    return os.getenv("UPLOADTHING_APP_ID") or None


async def upload_file_bytes(data: bytes, *, filename: str, content_type: str) -> str:
    """Upload original bytes (no transcoding). Returns the public file URL.

    v7 flow: ask UploadThing for a presigned ingest URL, then PUT the bytes.
    (The old v6 uploadFiles endpoint only returns presigned URLs and never
    accepted multipart bytes, so every server upload used to fail.)
    """
    api_key = uploadthing_api_key()
    if not api_key:
        raise RuntimeError("uploadthing_unconfigured")
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        prep = await client.post(
            PREPARE_URL,
            headers={"x-uploadthing-api-key": api_key, "content-type": "application/json"},
            json={
                "fileName": filename,
                "fileSize": len(data),
                "fileType": content_type,
                "acl": "public-read",
                "contentDisposition": "inline",
            },
        )
        if prep.status_code >= 400:
            logger.warning("UploadThing prepareUpload returned %s: %s", prep.status_code, prep.text[:300])
            raise RuntimeError("upload_failed")
        try:
            info = prep.json()
            key, put_url = str(info["key"]), str(info["url"])
        except Exception as exc:
            raise RuntimeError("upload_failed") from exc
        put = await client.put(put_url, files={"file": (filename, data, content_type)})
    if put.status_code >= 400:
        logger.warning("UploadThing ingest PUT returned %s: %s", put.status_code, put.text[:300])
        raise RuntimeError("upload_failed")
    url = None
    try:
        body = put.json()
        if isinstance(body, dict):
            url = body.get("ufsUrl") or body.get("url") or body.get("fileUrl")
    except Exception:  # noqa: BLE001
        url = None
    if not url:
        app_id = uploadthing_app_id()
        url = f"https://{app_id}.ufs.sh/f/{key}" if app_id else f"https://utfs.io/f/{key}"
    return str(url)


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
