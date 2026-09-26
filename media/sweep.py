"""Remove image URLs that UploadThing or Unsplash now answer with 404/410."""
from __future__ import annotations

import logging

from postgrest.exceptions import APIError

from core.postgrest_compat import is_undefined_column_error
from media.keys import keys_from_urls, normalize_urls
from media.validate import ProbeResult, head_media_urls

logger = logging.getLogger(__name__)

# These render as text cards when they have no photos.
_TEXT_CARD_TYPES = frozenset({"service", "opportunity", "job"})
_PAGE = 200


def classify_probe(result: ProbeResult | None) -> str:
    """`dead` only for a real 404 or 410. Timeouts and other statuses stay."""
    if result is None or result.timed_out or result.status is None:
        return "unknown"
    if result.status in {404, 410}:
        return "dead"
    return "ok"


def plan_listing_update(row: dict, statuses: dict[str, str]) -> dict | None:
    urls = normalize_urls(row.get("image_urls"))
    if not urls:
        return None
    removed = [url for url in urls if statuses.get(url) == "dead"]
    if not removed:
        return None
    kept = [url for url in urls if statuses.get(url) != "dead"]
    image_keys, video_keys = keys_from_urls(kept)
    update = {
        "image_urls": kept,
        "image_keys": image_keys,
        "video_keys": video_keys,
    }
    if kept:
        return update
    item_type = str(row.get("item_type") or "product").strip().lower()
    if item_type not in _TEXT_CARD_TYPES:
        meta = row.get("listing_meta") if isinstance(row.get("listing_meta"), dict) else {}
        merged = dict(meta)
        merged["needs_media"] = True
        update["listing_meta"] = merged
    return update


def summarize_sweep(rows: list[dict], planned: list[tuple[dict, dict]]) -> dict:
    checked_urls = 0
    seen: set[str] = set()
    for row in rows:
        for url in normalize_urls(row.get("image_urls")):
            if url not in seen:
                seen.add(url)
                checked_urls += 1
    removed = 0
    cleared = 0
    flagged = 0
    for row, update in planned:
        before = normalize_urls(row.get("image_urls"))
        after = update.get("image_urls") or []
        removed += len(before) - len(after)
        if not after:
            item_type = str(row.get("item_type") or "product").strip().lower()
            if item_type in _TEXT_CARD_TYPES:
                cleared += 1
            else:
                flagged += 1
    return {
        "checked_listings": len(rows),
        "checked_urls": checked_urls,
        "removed_urls": removed,
        "cleared_listings": cleared,
        "flagged_listings": flagged,
        "updated_listings": len(planned),
    }


def _load_active(client) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    columns = "id,image_urls,item_type,listing_meta,status,is_published"
    while True:
        result = (
            client.table("products")
            .select(columns)
            .eq("status", "active")
            .eq("is_published", True)
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        offset += _PAGE


def _save_listing(client, product_id: str, update: dict) -> None:
    try:
        client.table("products").update(update).eq("id", product_id).execute()
    except APIError as exc:
        if not (
            is_undefined_column_error(exc, "image_keys")
            or is_undefined_column_error(exc, "video_keys")
        ):
            raise
        slim = {key: value for key, value in update.items() if key not in {"image_keys", "video_keys"}}
        logger.warning(
            "products.image_keys missing while sweeping %s; run migration 045",
            product_id,
        )
        client.table("products").update(slim).eq("id", product_id).execute()


async def run_dead_image_sweep(client, *, head=None) -> dict:
    rows = [row for row in _load_active(client) if normalize_urls(row.get("image_urls"))]
    urls: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for url in normalize_urls(row.get("image_urls")):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    if head is None:
        probed = await head_media_urls(urls)
    else:
        probed = {}
        for url in urls:
            try:
                result = head(url)
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                logger.warning("dead-image HEAD failed for %s: %s", url, exc)
                result = ProbeResult(timed_out=True)
            if not isinstance(result, ProbeResult):
                status, content_type = result
                result = ProbeResult(status=status, content_type=content_type or "")
            probed[url] = result
    statuses = {url: classify_probe(probed.get(url)) for url in urls}
    planned: list[tuple[dict, dict]] = []
    errors = 0
    for row in rows:
        update = plan_listing_update(row, statuses)
        if update is None:
            continue
        planned.append((dict(row), update))
        try:
            _save_listing(client, str(row.get("id")), update)
        except Exception:
            logger.exception("dead-image update failed for %s", row.get("id"))
            errors += 1
    summary = summarize_sweep(rows, planned)
    summary["errors"] = errors
    return summary
