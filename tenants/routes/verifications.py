"""Shop verification endpoints (merchant + admin) — 3-stage architecture.

Stage model (stored in shop_verifications.metadata):
  Stage 1 — "Shop Listed"    — auto-granted on creation, badge: shop_listed
  Stage 2 — "Identity"       — NIN / ID docs / profile photo, badge: identity_verified
  Stage 3 — "Business"       — physical shop photo / registration / tax, badge: business_verified

The table still has a single (shop_id-unique) row in shop_verifications.
Stage state lives in the metadata JSONB column:
  {
    "badges": ["shop_listed", "identity_verified"],
    "stage2_status": "verified",   // unverified | pending | verified | rejected
    "stage3_status": "unverified",
    "stage2_notes": "...",
    "stage3_notes": "...",
    "stage2_docs": [...],
    "stage3_docs": [...],
    "stage2_requested_at": "...",
    "stage3_requested_at": "...",
  }

The top-level `status` column reflects the *latest active stage* submission
(pending/verified/rejected) so existing admin queries still work.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.authz import ensure_shop_owner
from core.security import get_current_user_id, get_optional_claims
from db.supabase import get_supabase_admin
from tenants.schemas_verifications import (
    VerificationDecisionRequest,
    VerificationResponse,
    VerificationSubmitRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_docs(raw: object) -> list[dict[str, Any]] | None:
    if isinstance(raw, list):
        return raw
    if raw is None:
        return None
    return None


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    """Safely extract metadata dict from a verification row."""
    m = row.get("metadata")
    if isinstance(m, dict):
        return m
    return {}


def _row_to_response(row: dict[str, Any]) -> VerificationResponse:
    meta = _meta(row)
    badges: list[str] = meta.get("badges") or []
    # Stage 1 badge is always present for any shop that has a verifications row.
    if "shop_listed" not in badges:
        badges = ["shop_listed"] + badges

    stage2_status = meta.get("stage2_status", "unverified")
    stage3_status = meta.get("stage3_status", "unverified")

    # current_stage: the highest stage that has activity
    if stage3_status in ("pending", "verified", "rejected"):
        current_stage = 3
    elif stage2_status in ("pending", "verified", "rejected"):
        current_stage = 2
    else:
        current_stage = 1

    # latest submitted docs (stage-specific stored in meta)
    submitted_docs = _normalise_docs(
        meta.get(f"stage{current_stage}_docs") or row.get("submitted_docs")
    )

    return VerificationResponse(
        id=str(row.get("id", "")),
        shop_id=str(row.get("shop_id", "")),
        status=row.get("status") or "unverified",
        requested_at=str(row["requested_at"]) if row.get("requested_at") else None,
        reviewed_at=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
        reviewed_by=str(row["reviewed_by"]) if row.get("reviewed_by") else None,
        notes=row.get("notes"),
        metadata=meta,
        submitted_docs=submitted_docs,
        submitted_phone=meta.get("stage2_phone") or row.get("submitted_phone"),
        submitted_whatsapp=meta.get("stage2_whatsapp") or row.get("submitted_whatsapp"),
        submitted_location=meta.get("stage2_location") or row.get("submitted_location"),
        shop_duration_days=row.get("shop_duration_days") or 0,
        current_stage=current_stage,
        badges=badges,
        stage2_status=stage2_status,
        stage3_status=stage3_status,
    )


def _get_or_create_verification_row(shop_id: str) -> dict[str, Any]:
    """Fetch the existing verifications row or create an empty one."""
    client = get_supabase_admin()
    r = (
        client.table("shop_verifications")
        .select("*")
        .eq("shop_id", shop_id)
        .limit(1)
        .execute()
    )
    if r.data:
        return r.data[0]
    # Create a minimal row with shop_listed badge
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "shop_id": shop_id,
        "status": "unverified",
        "metadata": {"badges": ["shop_listed"], "stage2_status": "unverified", "stage3_status": "unverified"},
        "requested_at": now_iso,
    }
    try:
        shop_r = client.table("shops").select("created_at").eq("id", shop_id).execute()
        if shop_r.data:
            created = shop_r.data[0].get("created_at")
            if created:
                delta = datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))
                payload["shop_duration_days"] = delta.days
    except Exception:
        pass
    ins = client.table("shop_verifications").insert(payload).execute()
    if ins.data:
        return ins.data[0]
    raise HTTPException(status_code=500, detail="Failed to initialise verification record")


def _resolve_merchant_email(client: Any, shop: dict[str, Any]) -> str | None:
    to_email = shop.get("shop_email")
    if to_email:
        return to_email
    owner_id = shop.get("owner_id")
    if not owner_id:
        return None
    u = client.table("users").select("email").eq("id", owner_id).limit(1).execute()
    if u.data:
        return u.data[0].get("email")
    return None


# ---------------------------------------------------------------------------
# Merchant-facing endpoints
# ---------------------------------------------------------------------------


@router.get("/{shop_id}/verification", response_model=VerificationResponse)
async def get_verification(
    shop_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> VerificationResponse:
    client = get_supabase_admin()
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    row = _get_or_create_verification_row(shop_id)
    return _row_to_response(row)


@router.post("/{shop_id}/verification/submit", response_model=VerificationResponse)
async def submit_for_verification(
    shop_id: str,
    body: VerificationSubmitRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> VerificationResponse:
    client = get_supabase_admin()
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    stage = max(2, min(3, body.stage))  # clamp to 2 or 3
    docs_dict = [d.model_dump() for d in (body.documents or [])] if body.documents else None

    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    # Stage 3 requires Stage 2 to be approved first
    if stage == 3 and meta.get("stage2_status") != "verified":
        raise HTTPException(
            status_code=400,
            detail="You must complete Identity Verification (Stage 2) before submitting Business Verification (Stage 3)."
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    # Update metadata for this stage
    meta[f"stage{stage}_status"] = "pending"
    meta[f"stage{stage}_requested_at"] = now_iso
    meta[f"stage{stage}_notes"] = body.notes
    if docs_dict:
        meta[f"stage{stage}_docs"] = docs_dict
    if stage == 2:
        if body.submitted_phone:
            meta["stage2_phone"] = body.submitted_phone
        if body.submitted_whatsapp:
            meta["stage2_whatsapp"] = body.submitted_whatsapp
        if body.submitted_location:
            meta["stage2_location"] = body.submitted_location

    payload: dict[str, Any] = {
        "status": "pending",
        "metadata": meta,
        "requested_at": now_iso,
        "reviewed_at": None,
        "reviewed_by": None,
        "notes": body.notes,
    }
    if stage == 2:
        if body.submitted_phone:
            payload["submitted_phone"] = body.submitted_phone
        if body.submitted_whatsapp:
            payload["submitted_whatsapp"] = body.submitted_whatsapp
        if body.submitted_location:
            payload["submitted_location"] = body.submitted_location
    if docs_dict:
        payload["submitted_docs"] = docs_dict

    existing = (
        client.table("shop_verifications")
        .select("id")
        .eq("shop_id", shop_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        r = (
            client.table("shop_verifications")
            .update(payload)
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        payload["shop_id"] = shop_id
        r = client.table("shop_verifications").insert(payload).execute()

    if not r.data:
        raise HTTPException(status_code=500, detail="Failed to save verification")

    # Send stage-specific emails (best-effort)
    await _send_stage_submission_emails(shop_id, stage, notes=body.notes)

    return _row_to_response(r.data[0])


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------


async def _send_stage_submission_emails(shop_id: str, stage: int, notes: str | None = None) -> None:
    try:
        from core.config import get_settings
        from mail.send import send_stage_submission_merchant_email, send_stage_submission_admin_email
        from mail.queue import get_admin_emails

        client = get_supabase_admin()
        r = client.table("shops").select("id, name, slug, shop_email, owner_id").eq("id", shop_id).limit(1).execute()
        if not r.data:
            return
        shop = r.data[0]
        merchant_email = _resolve_merchant_email(client, shop)

        if merchant_email:
            try:
                await send_stage_submission_merchant_email(merchant_email, shop.get("name", ""), stage)
            except Exception as exc:
                logger.warning("stage submission merchant email failed: %s", exc)

        admin_emails = get_admin_emails()
        if admin_emails:
            try:
                await send_stage_submission_admin_email(
                    admin_recipients=admin_emails,
                    shop_name=shop.get("name", ""),
                    shop_id=str(shop.get("id", "")),
                    stage=stage,
                    merchant_email=merchant_email,
                )
            except Exception as exc:
                logger.warning("stage submission admin email failed: %s", exc)
    except Exception as exc:
        logger.warning("stage submission email pipeline failed for shop %s: %s", shop_id, exc)


async def _send_stage_decision_email(shop_id: str, stage: int, decision: str, notes: str | None = None) -> None:
    try:
        from mail.send import send_stage_approved_email, send_stage_rejected_email

        client = get_supabase_admin()
        r = client.table("shops").select("name, shop_email, owner_id").eq("id", shop_id).limit(1).execute()
        if not r.data:
            return
        shop = r.data[0]
        to_email = _resolve_merchant_email(client, shop)
        if not to_email:
            return
        if decision == "approved":
            await send_stage_approved_email(to_email, shop.get("name", ""), stage)
        else:
            await send_stage_rejected_email(to_email, shop.get("name", ""), stage, notes)
    except Exception as exc:
        logger.warning("stage decision email failed for shop %s: %s", shop_id, exc)


# Keep backward-compat helpers used by old code paths
async def _send_verification_email(shop_id: str, decision: str, notes: str | None = None) -> None:
    """Legacy: used by the old approve/reject endpoints."""
    try:
        from mail.send import send_shop_verification_decision_email
        client = get_supabase_admin()
        r = client.table("shops").select("name, shop_email, owner_id").eq("id", shop_id).limit(1).execute()
        if not r.data:
            return
        shop = r.data[0]
        to_email = _resolve_merchant_email(client, shop)
        if not to_email:
            return
        await send_shop_verification_decision_email(to_email, shop.get("name", ""), decision, notes)
    except Exception as exc:
        logger.warning("verification email failed for shop %s: %s", shop_id, exc)


async def _send_submission_emails(shop_id: str, notes: str | None = None) -> None:
    """Legacy: backward compat used by queue/admin shortcuts."""
    try:
        from core.config import get_settings
        from mail.send import send_new_shop_submission_admin_email, send_shop_submission_received_email
        from mail.queue import get_admin_emails

        client = get_supabase_admin()
        r = client.table("shops").select("id, name, slug, shop_email, owner_id").eq("id", shop_id).limit(1).execute()
        if not r.data:
            return
        shop = r.data[0]
        merchant_email = _resolve_merchant_email(client, shop)

        if merchant_email:
            try:
                await send_shop_submission_received_email(merchant_email, shop.get("name", ""))
            except Exception as exc:
                logger.warning("submission confirmation email failed: %s", exc)

        admin_emails = get_admin_emails()
        if admin_emails:
            try:
                await send_new_shop_submission_admin_email(
                    admin_recipients=admin_emails,
                    shop_name=shop.get("name", ""),
                    shop_slug=shop.get("slug"),
                    shop_id=str(shop.get("id", "")),
                    merchant_email=merchant_email,
                    notes=notes,
                )
            except Exception as exc:
                logger.warning("admin submission email failed: %s", exc)
    except Exception as exc:
        logger.warning("submission email pipeline failed for shop %s: %s", shop_id, exc)


# ---------------------------------------------------------------------------
# Admin-facing endpoints
# ---------------------------------------------------------------------------


admin_router = APIRouter()


@admin_router.get("/verifications")
async def admin_list_verifications(
    status: Annotated[str | None, Query()] = "pending",
    stage: Annotated[int | None, Query(description="Filter by verification stage (2 or 3)")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_unverified: Annotated[bool, Query()] = False,
):
    """List shop verifications. Filter by stage (2=Identity, 3=Business) via ?stage=2."""
    client = get_supabase_admin()
    q = (
        client.table("shop_verifications")
        .select("*, shops(name, slug, owner_id, shop_email, is_active, created_at)")
        .order("requested_at", desc=True)
        .limit(limit)
    )
    if status and status != "all":
        q = q.eq("status", status)
    r = q.execute()
    items: list[dict[str, Any]] = list(r.data or [])

    # Filter by stage if requested (stage info lives in metadata)
    if stage in (2, 3):
        stage_key = f"stage{stage}_status"
        stage_status_filter = status if status and status != "all" else None
        filtered = []
        for item in items:
            meta = item.get("metadata") or {}
            if isinstance(meta, dict):
                s_status = meta.get(stage_key, "unverified")
                if stage_status_filter:
                    if s_status == stage_status_filter:
                        filtered.append(item)
                else:
                    if s_status != "unverified":
                        filtered.append(item)
        items = filtered

    # Optionally fold in shops with no verifications row yet.
    if include_unverified and (status in (None, "all", "unverified")):
        try:
            covered_ids = {row.get("shop_id") for row in items if row.get("shop_id")}
            shop_rows = (
                client.table("shops")
                .select("id, name, slug, owner_id, shop_email, is_active, created_at")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            for s in shop_rows.data or []:
                if s["id"] in covered_ids:
                    continue
                items.append({
                    "id": "",
                    "shop_id": s["id"],
                    "status": "unverified",
                    "metadata": {"badges": [], "stage2_status": "unverified", "stage3_status": "unverified"},
                    "requested_at": None,
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "notes": None,
                    "shops": {
                        "name": s.get("name"),
                        "slug": s.get("slug"),
                        "owner_id": s.get("owner_id"),
                        "shop_email": s.get("shop_email"),
                        "is_active": s.get("is_active"),
                        "created_at": s.get("created_at"),
                    },
                })
        except Exception:
            logger.exception("include_unverified expansion failed")

    return {"items": items}


@admin_router.get("/verifications/{shop_id}/stages")
async def admin_get_verification_stages(shop_id: str) -> dict[str, Any]:
    """Return full stage breakdown for a single shop."""
    client = get_supabase_admin()
    r = client.table("shop_verifications").select("*").eq("shop_id", shop_id).limit(1).execute()
    if not r.data:
        return {
            "shop_id": shop_id,
            "badges": ["shop_listed"],
            "stage1": {"status": "verified", "auto": True},
            "stage2": {"status": "unverified"},
            "stage3": {"status": "unverified"},
        }
    row = r.data[0]
    meta = _meta(row)
    return {
        "shop_id": shop_id,
        "badges": meta.get("badges") or ["shop_listed"],
        "stage1": {"status": "verified", "auto": True},
        "stage2": {
            "status": meta.get("stage2_status", "unverified"),
            "requested_at": meta.get("stage2_requested_at"),
            "notes": meta.get("stage2_notes"),
            "docs": meta.get("stage2_docs"),
        },
        "stage3": {
            "status": meta.get("stage3_status", "unverified"),
            "requested_at": meta.get("stage3_requested_at"),
            "notes": meta.get("stage3_notes"),
            "docs": meta.get("stage3_docs"),
        },
    }


@admin_router.post("/verifications/{shop_id}/queue", response_model=VerificationResponse)
async def admin_queue_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    claims=Depends(get_optional_claims),
) -> VerificationResponse:
    """Admin shortcut: push a shop into the verification queue."""
    notes = body.notes if body else None
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)
    meta["stage2_status"] = "pending"
    meta.setdefault("badges", ["shop_listed"])
    now_iso = datetime.now(timezone.utc).isoformat()
    meta["stage2_requested_at"] = now_iso

    client = get_supabase_admin()
    r = (
        client.table("shop_verifications")
        .update({"status": "pending", "metadata": meta, "requested_at": now_iso, "notes": notes})
        .eq("shop_id", shop_id)
        .execute()
    )
    try:
        await _send_submission_emails(shop_id, notes=notes)
    except Exception:
        logger.exception("queue verification email pipeline failed")
    return _row_to_response(r.data[0] if r.data else row)


@admin_router.post("/verifications/{shop_id}/approve")
async def admin_approve_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    stage: int = Query(2, description="Which stage to approve (2 or 3)"),
    claims=Depends(get_optional_claims),
) -> dict[str, Any]:
    """Approve a specific verification stage and grant the corresponding badge."""
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None
    stage = max(2, min(3, stage))

    client = get_supabase_admin()
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    badge_map = {2: "identity_verified", 3: "business_verified"}
    badge = badge_map[stage]

    meta[f"stage{stage}_status"] = "verified"
    meta[f"stage{stage}_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    meta[f"stage{stage}_reviewer"] = reviewer_id
    meta[f"stage{stage}_notes"] = notes
    badges = meta.get("badges") or ["shop_listed"]
    if badge not in badges:
        badges.append(badge)
    meta["badges"] = badges

    # Determine top-level status: verified if any stage is verified
    top_status = "verified"

    now_iso = datetime.now(timezone.utc).isoformat()
    upd = (
        client.table("shop_verifications")
        .update({
            "status": top_status,
            "reviewed_at": now_iso,
            "reviewed_by": reviewer_id,
            "notes": notes,
            "metadata": meta,
        })
        .eq("shop_id", shop_id)
        .execute()
    )

    # Make shop active when stage 2 is approved
    if stage == 2:
        act = client.table("shops").update({
            "is_active": True,
            "trust_badges": badges
        }).eq("id", shop_id).execute()
        if act.data:
            owner_id = act.data[0].get("owner_id")
            if owner_id:
                from auth import service as auth_service
                auth_service.promote_to_merchant(str(owner_id))
    else:
        client.table("shops").update({
            "trust_badges": badges
        }).eq("id", shop_id).execute()

    await _send_stage_decision_email(shop_id, stage, "approved", notes)

    result = upd.data[0] if upd.data else row
    return _row_to_response(result).model_dump()


@admin_router.post("/verifications/{shop_id}/reject")
async def admin_reject_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    stage: int = Query(2, description="Which stage to reject (2 or 3)"),
    claims=Depends(get_optional_claims),
) -> dict[str, Any]:
    """Reject a specific verification stage with optional reviewer notes."""
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None
    stage = max(2, min(3, stage))

    client = get_supabase_admin()
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    meta[f"stage{stage}_status"] = "rejected"
    meta[f"stage{stage}_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    meta[f"stage{stage}_reviewer"] = reviewer_id
    meta[f"stage{stage}_notes"] = notes

    now_iso = datetime.now(timezone.utc).isoformat()
    upd = (
        client.table("shop_verifications")
        .update({
            "status": "rejected",
            "reviewed_at": now_iso,
            "reviewed_by": reviewer_id,
            "notes": notes,
            "metadata": meta,
        })
        .eq("shop_id", shop_id)
        .execute()
    )

    if stage == 2:
        client.table("shops").update({"is_active": False}).eq("id", shop_id).execute()

    await _send_stage_decision_email(shop_id, stage, "rejected", notes)

    result = upd.data[0] if upd.data else row
    return _row_to_response(result).model_dump()
