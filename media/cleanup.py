"""Background deletion of UploadThing files that no listing still uses."""
from __future__ import annotations

import logging

from media.keys import normalize_urls, removed_file_keys
from media.references import keys_safe_to_delete, load_referenced_keys
from media.uploadthing import delete_file_keys

logger = logging.getLogger(__name__)


async def cleanup_removed_media(old_urls, new_urls, *, client=None, delete_files=None) -> list[str]:
    """Delete UploadThing keys present in `old_urls` and absent from `new_urls`.

    A key still stored on any product, shop logo, or avatar is kept.
    Missing API credentials are logged inside `delete_file_keys` and skipped.
    """
    removed = removed_file_keys(normalize_urls(old_urls), normalize_urls(new_urls))
    if not removed:
        return []
    db = client
    if db is None:
        from db.supabase import get_supabase_admin

        db = get_supabase_admin()
    try:
        referenced = load_referenced_keys(db)
    except Exception:
        return []
    safe = keys_safe_to_delete(removed, referenced)
    if not safe:
        return []
    deleter = delete_files or delete_file_keys
    try:
        await deleter(safe)
    except Exception:
        logger.exception("UploadThing delete failed for %s", safe)
        return []
    return safe
