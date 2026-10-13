"""Track who uploaded each UploadThing file.

Rows are written by the website's UploadThing onUploadComplete (server to
server). Users may only discard files they uploaded that nothing uses yet.
Unused uploads older than ORPHAN_AGE are swept by the media cron.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from postgrest.exceptions import APIError

from media.keys import file_key_from_url
from media.references import load_referenced_keys
from media.uploadthing import delete_file_keys

logger = logging.getLogger(__name__)

TABLE = "media_uploads"
KINDS = frozenset(
    {"listingPhoto", "shopLogo", "shopBanner", "verificationDoc", "avatar", "productVideo"}
)
ORPHAN_AGE = timedelta(hours=24)
MAX_DISCARD = 20


def _is_missing_table(exc: BaseException) -> bool:
    return isinstance(exc, APIError) and exc.code in {"42P01", "PGRST205"}


def register_upload(client, *, key: str, url: str, user_id: str, kind: str) -> bool:
    key = (key or "").strip() or (file_key_from_url(url) or "")
    if not key or not url or not user_id or kind not in KINDS:
        raise ValueError("key, url, user_id and a known kind are required")
    try:
        client.table(TABLE).upsert(
            {"key": key, "url": url, "user_id": user_id, "kind": kind},
            on_conflict="key",
        ).execute()
    except APIError as exc:
        if _is_missing_table(exc):
            logger.warning("media_uploads table missing; run migration 052")
            return False
        raise
    return True


def requested_keys(keys, urls) -> list[str]:
    out: list[str] = []
    for raw in list(keys or []):
        if isinstance(raw, str) and raw.strip():
            out.append(raw.strip())
    for url in list(urls or []):
        if isinstance(url, str):
            key = file_key_from_url(url)
            if key:
                out.append(key)
    return list(dict.fromkeys(out))[:MAX_DISCARD]


def discardable(owned_rows: list[dict], user_id: str, referenced: set[str]) -> list[str]:
    """Keys the user uploaded that are still unused anywhere."""
    safe: list[str] = []
    for row in owned_rows:
        key = row.get("key")
        if not key or str(row.get("user_id")) != str(user_id):
            continue
        if key in referenced:
            continue
        safe.append(key)
    return safe


async def discard_uploads(client, user_id: str, keys: list[str], *, delete_files=None) -> dict:
    if not keys:
        return {"deleted": 0, "skipped": 0}
    try:
        rows = (
            client.table(TABLE)
            .select("key,user_id")
            .in_("key", keys)
            .eq("user_id", user_id)
            .execute()
        ).data or []
    except APIError as exc:
        if _is_missing_table(exc):
            return {"deleted": 0, "skipped": len(keys)}
        raise
    if not rows:
        return {"deleted": 0, "skipped": len(keys)}
    referenced = load_referenced_keys(client)
    safe = discardable(rows, user_id, referenced)
    attached = [r["key"] for r in rows if r.get("key") in referenced]
    if attached:
        _mark_attached(client, attached)
    if safe:
        await (delete_files or delete_file_keys)(safe)
        client.table(TABLE).delete().in_("key", safe).execute()
    return {"deleted": len(safe), "skipped": len(keys) - len(safe)}


def _mark_attached(client, keys: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(keys), 100):
        client.table(TABLE).update({"attached_at": now}).in_(
            "key", keys[start:start + 100]
        ).is_("attached_at", "null").execute()


async def sweep_orphan_uploads(client, *, now: datetime | None = None, delete_files=None) -> dict:
    """Delete uploads nobody attached within ORPHAN_AGE; mark used ones attached."""
    cutoff = (now or datetime.now(timezone.utc)) - ORPHAN_AGE
    try:
        rows = (
            client.table(TABLE)
            .select("key,created_at")
            .is_("attached_at", "null")
            .lt("created_at", cutoff.isoformat())
            .limit(500)
            .execute()
        ).data or []
    except APIError as exc:
        if _is_missing_table(exc):
            return {"orphans_deleted": 0, "attached": 0}
        raise
    if not rows:
        return {"orphans_deleted": 0, "attached": 0}
    referenced = load_referenced_keys(client)
    used = [r["key"] for r in rows if r["key"] in referenced]
    orphans = [r["key"] for r in rows if r["key"] not in referenced]
    if used:
        _mark_attached(client, used)
    if orphans:
        await (delete_files or delete_file_keys)(orphans)
        for start in range(0, len(orphans), 100):
            client.table(TABLE).delete().in_("key", orphans[start:start + 100]).execute()
    return {"orphans_deleted": len(orphans), "attached": len(used)}
