"""Supabase-backed persistence for the moderation queue.

Everything here is thin: enqueue, claim-batch (via RPC), and write-back.
Business logic (thresholds, stage orchestration) lives in `pipeline.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from db.supabase import get_supabase_admin

from .schemas import ModerationDecision, ModerationRow, ModerationStatus, SubmitListingRequest

logger = logging.getLogger(__name__)

_TABLE = "listing_moderation_queue"
_BAD_HASHES_TABLE = "moderation_bad_image_hashes"


def enqueue(payload: SubmitListingRequest) -> ModerationRow:
    admin = get_supabase_admin()
    row = {
        "product_id": str(payload.product_id) if payload.product_id else None,
        "seller_id": str(payload.seller_id) if payload.seller_id else None,
        "title": payload.title,
        "description": payload.description or "",
        "image_urls": payload.image_urls or [],
    }
    r = admin.table(_TABLE).insert(row).execute()
    if not r.data:
        raise RuntimeError("Failed to enqueue moderation row")
    return ModerationRow.model_validate(r.data[0])


def get_by_id(row_id: UUID) -> Optional[ModerationRow]:
    admin = get_supabase_admin()
    r = admin.table(_TABLE).select("*").eq("id", str(row_id)).limit(1).execute()
    if not r.data:
        return None
    return ModerationRow.model_validate(r.data[0])


def list_by_status(status: ModerationStatus, limit: int = 100) -> list[ModerationRow]:
    admin = get_supabase_admin()
    r = (
        admin.table(_TABLE)
        .select("*")
        .eq("status", status.value)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [ModerationRow.model_validate(row) for row in (r.data or [])]


def reclaim_stuck(older_than_seconds: int) -> int:
    """Reset rows stuck in 'processing' back to 'pending'. Returns count."""
    admin = get_supabase_admin()
    try:
        r = admin.rpc(
            "reclaim_stuck_moderation_rows",
            {"p_older_than_seconds": older_than_seconds},
        ).execute()
        return int(r.data or 0)
    except Exception as exc:
        logger.warning("reclaim_stuck failed: %s", exc)
        return 0


def claim_batch(limit: int) -> list[ModerationRow]:
    """Atomically claim up to `limit` pending rows (FOR UPDATE SKIP LOCKED)."""
    admin = get_supabase_admin()
    r = admin.rpc("claim_moderation_queue_batch", {"p_limit": limit}).execute()
    return [ModerationRow.model_validate(row) for row in (r.data or [])]


def claim_by_id(row_id: UUID | str) -> Optional[ModerationRow]:
    """Claim a specific row and flip it to 'processing'.

    Used by the inline hook so create_product can process the row it just
    enqueued without racing the cron drain. Returns None when the row was
    already claimed by a concurrent drain, or is no longer pending.
    """
    admin = get_supabase_admin()
    try:
        # Idempotent status transition: pending -> processing. Any drain
        # that already grabbed the row will win here (no rows updated).
        r = (
            admin.table(_TABLE)
            .update({
                "status": "processing",
                "processing_started_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", str(row_id))
            .eq("status", "pending")
            .execute()
        )
    except Exception as exc:
        logger.warning("claim_by_id failed for %s: %s", row_id, exc)
        return None
    if not r.data:
        return None
    return ModerationRow.model_validate(r.data[0])


def latest_pending_row_for_product(product_id: UUID | str) -> Optional[UUID]:
    """Return the id of the most recent pending queue row for a product.

    The async route handler uses this right after `shop_service.create_product`
    (or `update_product`) to pick up the row that was just enqueued and drive
    the pipeline inline via `moderate_now`. Returns None when no row is
    pending (enqueue failed, or an eager cron drain already picked it up).
    """
    admin = get_supabase_admin()
    try:
        r = (
            admin.table(_TABLE)
            .select("id")
            .eq("product_id", str(product_id))
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("latest_pending_row_for_product failed: %s", exc)
        return None
    if not r.data:
        return None
    try:
        return UUID(str(r.data[0]["id"]))
    except (KeyError, ValueError, TypeError):
        return None


def write_decision(row_id: UUID, decision: ModerationDecision) -> None:
    admin = get_supabase_admin()
    update = {
        "status": decision.status.value,
        "reason": decision.reason,
        "scores": decision.scores,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    admin.table(_TABLE).update(update).eq("id", str(row_id)).execute()


def mark_failed(row_id: UUID, error: str) -> None:
    admin = get_supabase_admin()
    admin.table(_TABLE).update({
        "status": ModerationStatus.FAILED.value,
        "error": error[:2000],
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", str(row_id)).execute()


def sync_product_status(product_id: UUID, decision: ModerationDecision) -> None:
    """Push the moderation outcome back onto the products row (if any).

    Maps our four decision states onto the existing products.status enum:
        approved      -> 'active'
        rejected      -> 'rejected'
        needs_review  -> 'pending_review'
        failed        -> leave alone (transient error)
    """
    mapping = {
        ModerationStatus.APPROVED: "active",
        ModerationStatus.REJECTED: "rejected",
        ModerationStatus.NEEDS_REVIEW: "pending_review",
    }
    target = mapping.get(decision.status)
    if not target:
        return

    admin = get_supabase_admin()
    admin.table("products").update({
        "status": target,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_notes": decision.reason,
    }).eq("id", str(product_id)).execute()


def load_bad_image_hashes() -> list[int]:
    """Read the current known-bad phash set. Small enough to fetch each drain."""
    admin = get_supabase_admin()
    try:
        r = admin.table(_BAD_HASHES_TABLE).select("phash").execute()
        return [int(row["phash"]) for row in (r.data or []) if row.get("phash") is not None]
    except Exception as exc:
        logger.warning("load_bad_image_hashes failed: %s", exc)
        return []


def add_bad_image_hash(phash: int, label: Optional[str], added_by: Optional[UUID]) -> None:
    admin = get_supabase_admin()
    admin.table(_BAD_HASHES_TABLE).insert({
        "phash": int(phash),
        "label": label,
        "added_by": str(added_by) if added_by else None,
    }).execute()


def fetch_products_pending_review(limit: int) -> list[dict[str, Any]]:
    """Products still awaiting a moderation decision (oldest first)."""
    admin = get_supabase_admin()
    try:
        r = (
            admin.table("products")
            .select("id,title,description,image_urls,shop_id,status")
            .eq("status", "pending_review")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception as exc:
        logger.warning("fetch_products_pending_review failed: %s", exc)
        return []


def load_queue_rows_for_products(product_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Map product_id -> its moderation queue rows (status/reason/scores)."""
    if not product_ids:
        return {}
    admin = get_supabase_admin()
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        r = (
            admin.table(_TABLE)
            .select("id,product_id,status,reason,scores")
            .in_("product_id", product_ids)
            .execute()
        )
        for row in r.data or []:
            pid = str(row.get("product_id"))
            out.setdefault(pid, []).append(row)
    except Exception as exc:
        logger.warning("load_queue_rows_for_products failed: %s", exc)
    return out

