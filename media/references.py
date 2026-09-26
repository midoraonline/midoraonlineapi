"""Which UploadThing keys are still used by a live record.

Scanned columns: products.image_urls / image_keys / video_keys,
shops.logo_url, profiles.avatar_url. Shops have no banner column.
"""
from __future__ import annotations

import logging

from postgrest.exceptions import APIError

from core.postgrest_compat import is_undefined_column_error
from media.keys import file_key_from_url, normalize_urls

logger = logging.getLogger(__name__)

_PAGE = 500


def referenced_key_set(products: list[dict], logos: list, avatars: list) -> set[str]:
    keys: set[str] = set()
    for row in products or []:
        for key in keys_on_product(row):
            keys.add(key)
    for url in list(logos or []) + list(avatars or []):
        key = file_key_from_url(url if isinstance(url, str) else "")
        if key:
            keys.add(key)
    return keys


def keys_on_product(row: dict) -> set[str]:
    found: set[str] = set()
    for url in normalize_urls(row.get("image_urls")):
        key = file_key_from_url(url)
        if key:
            found.add(key)
    for column in ("image_keys", "video_keys"):
        raw = row.get(column) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for item in raw:
            key = str(item or "").strip()
            if key:
                found.add(key)
    return found


def keys_safe_to_delete(removed: list[str], referenced: set[str]) -> list[str]:
    """Keys that left this listing and are not used anywhere else."""
    safe: list[str] = []
    seen: set[str] = set()
    for key in removed:
        if not key or key in seen or key in referenced:
            continue
        seen.add(key)
        safe.append(key)
    return safe


def _rows(client, table: str, columns: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        result = (
            client.table(table)
            .select(columns)
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        offset += _PAGE


def load_referenced_keys(client) -> set[str]:
    """Fail closed: a scan error must not be treated as 'nothing references this'."""
    try:
        try:
            products = _rows(client, "products", "image_urls,image_keys,video_keys")
        except APIError as exc:
            if not (
                is_undefined_column_error(exc, "image_keys")
                or is_undefined_column_error(exc, "video_keys")
            ):
                raise
            products = _rows(client, "products", "image_urls")
        shops = _rows(client, "shops", "logo_url")
        profiles = _rows(client, "profiles", "avatar_url")
    except Exception:
        logger.exception("UploadThing reference scan failed; refusing to delete files")
        raise
    logos = [row.get("logo_url") for row in shops if row.get("logo_url")]
    avatars = [row.get("avatar_url") for row in profiles if row.get("avatar_url")]
    return referenced_key_set(products, logos, avatars)
