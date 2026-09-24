"""Shop verification endpoints (merchant + admin) — Phase 3 ladder.

Stage model (shop_verifications.metadata):
  Stage 1 — Shop Listed (auto) — badge: shop_listed
  Stage 2 — Identity — NIN / passport / driving permit + selfie — badge: identity_verified
  Stage 3 — Business — registration / TIN / shop photo — badge: business_verified
  Stage 4 — Professional — credentials where relevant — badge: professional_verified

Capture vs review:
  request_review=False → status/stage = "submitted" (docs stored; not in admin queue)
  request_review=True  → status/stage = "pending" (manual review queue + emails)

Top-level `status` reflects the latest active submission so admin filters work.
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
    VerificationRequestReviewBody,
    VerificationResponse,
    VerificationSubmitRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

BADGE_MAP = {
    2: "identity_verified",
    3: "business_verified",
    4: "professional_verified",
}
VALID_STAGES = (2, 3, 4)


def _normalise_docs(raw: object) -> list[dict[str, Any]] | None:
    if isinstance(raw, list):
        return raw
    if raw is None:
        return None
    return None


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    m = row.get("metadata")
    if isinstance(m, dict):
        return m
    return {}


def _clamp_stage(stage: int) -> int:
    return max(2, min(4, int(stage or 2)))


def _row_to_response(row: dict[str, Any]) -> VerificationResponse:
    meta = _meta(row)
    badges: list[str] = list(meta.get("badges") or [])
    if "shop_listed" not in badges:
        badges = ["shop_listed"] + badges

    stage2_status = meta.get("stage2_status", "unverified")
    stage3_status = meta.get("stage3_status", "unverified")
    stage4_status = meta.get("stage4_status", "unverified")

    if stage4_status in ("submitted", "pending", "verified", "rejected"):
        current_stage = 4
    elif stage3_status in ("submitted", "pending", "verified", "rejected"):
        current_stage = 3
    elif stage2_status in ("submitted", "pending", "verified", "rejected"):
        current_stage = 2
    else:
        current_stage = 1

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
        stage4_status=stage4_status,
    )


def _get_or_create_verification_row(shop_id: str) -> dict[str, Any]:
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
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "shop_id": shop_id,
        "status": "unverified",
        "metadata": {
            "badges": ["shop_listed"],
            "stage2_status": "unverified",
            "stage3_status": "unverified",
            "stage4_status": "unverified",
        },
        "requested_at": now_iso,
    }
    try:
        shop_r = client.table("shops").select("created_at").eq("id", shop_id).execute()
        if shop_r.data:
            created = shop_r.data[0].get("created_at")
            if created:
                delta = datetime.now(timezone.utc) - datetime.fromisoformat(
                    created.replace("Z", "+00:00")
                )
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


def _persist_verification(client: Any, shop_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        payload = {**payload, "shop_id": shop_id}
        r = client.table("shop_verifications").insert(payload).execute()
    if not r.data:
        raise HTTPException(status_code=500, detail="Failed to save verification")
    return r.data[0]


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

    stage = _clamp_stage(body.stage)
    docs_dict = [d.model_dump() for d in (body.documents or [])] if body.documents else None
    if not docs_dict:
        raise HTTPException(status_code=400, detail="Upload at least one document (e.g. ID + selfie).")

    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    if stage == 3 and meta.get("stage2_status") != "verified":
        raise HTTPException(
            status_code=400,
            detail="Complete Identity Verification before Business Verification.",
        )
    if stage == 4 and meta.get("stage2_status") != "verified":
        raise HTTPException(
            status_code=400,
            detail="Complete Identity Verification before Professional Verification.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    stage_status = "pending" if body.request_review else "submitted"
    meta[f"stage{stage}_status"] = stage_status
    meta[f"stage{stage}_requested_at"] = now_iso
    meta[f"stage{stage}_notes"] = body.notes
    meta[f"stage{stage}_docs"] = docs_dict
    meta.setdefault("badges", ["shop_listed"])
    meta.setdefault("stage4_status", "unverified")

    if stage == 2:
        if body.submitted_phone:
            meta["stage2_phone"] = body.submitted_phone
        if body.submitted_whatsapp:
            meta["stage2_whatsapp"] = body.submitted_whatsapp
        if body.submitted_location:
            meta["stage2_location"] = body.submitted_location

    payload: dict[str, Any] = {
        "status": stage_status,
        "metadata": meta,
        "requested_at": now_iso,
        "reviewed_at": None,
        "reviewed_by": None,
        "notes": body.notes,
        "submitted_docs": docs_dict,
    }
    if stage == 2:
        if body.submitted_phone:
            payload["submitted_phone"] = body.submitted_phone
        if body.submitted_whatsapp:
            payload["submitted_whatsapp"] = body.submitted_whatsapp
        if body.submitted_location:
            payload["submitted_location"] = body.submitted_location

    saved = _persist_verification(client, shop_id, payload)

    if body.request_review:
        await _send_stage_submission_emails(shop_id, stage, notes=body.notes)

    return _row_to_response(saved)


@router.post("/{shop_id}/verification/request-review", response_model=VerificationResponse)
async def request_verification_review(
    shop_id: str,
    body: VerificationRequestReviewBody,
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> VerificationResponse:
    """Move captured (submitted) docs into the admin review queue."""
    client = get_supabase_admin()
    try:
        ensure_shop_owner(client, shop_id, user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Shop not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    stage = _clamp_stage(body.stage)
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)
    current = meta.get(f"stage{stage}_status", "unverified")
    docs = meta.get(f"stage{stage}_docs") or row.get("submitted_docs")
    if current not in ("submitted", "rejected") and not docs:
        raise HTTPException(
            status_code=400,
            detail="Capture ID / documents first, then request review.",
        )
    if current == "verified":
        raise HTTPException(status_code=400, detail="This stage is already verified.")
    if current == "pending":
        return _row_to_response(row)

    now_iso = datetime.now(timezone.utc).isoformat()
    meta[f"stage{stage}_status"] = "pending"
    meta[f"stage{stage}_requested_at"] = now_iso
    if body.notes:
        meta[f"stage{stage}_notes"] = body.notes

    saved = _persist_verification(
        client,
        shop_id,
        {
            "status": "pending",
            "metadata": meta,
            "requested_at": now_iso,
            "notes": body.notes or row.get("notes"),
        },
    )
    await _send_stage_submission_emails(shop_id, stage, notes=body.notes)
    return _row_to_response(saved)


async def _send_stage_submission_emails(shop_id: str, stage: int, notes: str | None = None) -> None:
    try:
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


async def _send_verification_email(shop_id: str, decision: str, notes: str | None = None) -> None:
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
    try:
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


admin_router = APIRouter()


@admin_router.get("/verifications")
async def admin_list_verifications(
    status: Annotated[str | None, Query()] = "pending",
    stage: Annotated[int | None, Query(description="Filter by stage 2/3/4")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_unverified: Annotated[bool, Query()] = False,
):
    """List verifications. Default status=pending (review queue only; excludes capture-only submitted)."""
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

    if stage in VALID_STAGES:
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
                elif s_status != "unverified":
                    filtered.append(item)
        items = filtered

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
                    "metadata": {
                        "badges": [],
                        "stage2_status": "unverified",
                        "stage3_status": "unverified",
                        "stage4_status": "unverified",
                    },
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
    client = get_supabase_admin()
    r = client.table("shop_verifications").select("*").eq("shop_id", shop_id).limit(1).execute()
    if not r.data:
        return {
            "shop_id": shop_id,
            "badges": ["shop_listed"],
            "stage1": {"status": "verified", "auto": True},
            "stage2": {"status": "unverified"},
            "stage3": {"status": "unverified"},
            "stage4": {"status": "unverified"},
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
            "docs": meta.get("stage2_docs") or _normalise_docs(row.get("submitted_docs")),
            "phone": meta.get("stage2_phone") or row.get("submitted_phone"),
            "whatsapp": meta.get("stage2_whatsapp") or row.get("submitted_whatsapp"),
            "location": meta.get("stage2_location") or row.get("submitted_location"),
        },
        "stage3": {
            "status": meta.get("stage3_status", "unverified"),
            "requested_at": meta.get("stage3_requested_at"),
            "notes": meta.get("stage3_notes"),
            "docs": meta.get("stage3_docs"),
        },
        "stage4": {
            "status": meta.get("stage4_status", "unverified"),
            "requested_at": meta.get("stage4_requested_at"),
            "notes": meta.get("stage4_notes"),
            "docs": meta.get("stage4_docs"),
        },
        "shop_duration_days": row.get("shop_duration_days") or 0,
        "submitted_phone": meta.get("stage2_phone") or row.get("submitted_phone"),
        "submitted_whatsapp": meta.get("stage2_whatsapp") or row.get("submitted_whatsapp"),
        "submitted_location": meta.get("stage2_location") or row.get("submitted_location"),
    }


@admin_router.post("/verifications/{shop_id}/queue", response_model=VerificationResponse)
async def admin_queue_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    claims=Depends(get_optional_claims),
) -> VerificationResponse:
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
    stage: int = Query(2, description="Stage to approve (2, 3, or 4)"),
    claims=Depends(get_optional_claims),
) -> dict[str, Any]:
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None
    stage = _clamp_stage(stage)

    client = get_supabase_admin()
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    badge = BADGE_MAP[stage]
    meta[f"stage{stage}_status"] = "verified"
    meta[f"stage{stage}_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    meta[f"stage{stage}_reviewer"] = reviewer_id
    meta[f"stage{stage}_notes"] = notes
    badges = list(meta.get("badges") or ["shop_listed"])
    if badge not in badges:
        badges.append(badge)
    meta["badges"] = badges

    now_iso = datetime.now(timezone.utc).isoformat()
    upd = (
        client.table("shop_verifications")
        .update({
            "status": "verified",
            "reviewed_at": now_iso,
            "reviewed_by": reviewer_id,
            "notes": notes,
            "metadata": meta,
        })
        .eq("shop_id", shop_id)
        .execute()
    )

    shop_upd: dict[str, Any] = {"trust_badges": badges}
    if stage == 2:
        shop_upd["is_active"] = True
    act = client.table("shops").update(shop_upd).eq("id", shop_id).execute()
    if stage == 2 and act.data:
        owner_id = act.data[0].get("owner_id")
        if owner_id:
            from auth import service as auth_service
            auth_service.promote_to_merchant(str(owner_id))

    try:
        from common.events.publishers import publish_shop_verification_changed
        await publish_shop_verification_changed(
            shop_id=shop_id,
            new_status="verified",
            stage=stage,
            reason=notes,
        )
    except Exception as exc:
        logger.warning("publish verification approval event failed for shop %s: %s", shop_id, exc)

    try:
        await _send_stage_decision_email(shop_id, stage, "approved", notes)
    except Exception:
        logger.exception("approve decision email failed")

    result = upd.data[0] if upd.data else row
    return _row_to_response(result).model_dump()


@admin_router.post("/verifications/{shop_id}/reject")
async def admin_reject_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    stage: int = Query(2, description="Stage to reject (2, 3, or 4)"),
    claims=Depends(get_optional_claims),
) -> dict[str, Any]:
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None
    stage = _clamp_stage(stage)

    client = get_supabase_admin()
    row = _get_or_create_verification_row(shop_id)
    meta = _meta(row)

    meta[f"stage{stage}_status"] = "rejected"
    meta[f"stage{stage}_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    meta[f"stage{stage}_reviewer"] = reviewer_id
    meta[f"stage{stage}_notes"] = notes

    # Drop badge for this stage if it was previously granted
    badge = BADGE_MAP[stage]
    badges = [b for b in (meta.get("badges") or ["shop_listed"]) if b != badge]
    if "shop_listed" not in badges:
        badges = ["shop_listed"] + badges
    meta["badges"] = badges

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

    # Do not deactivate shop on identity reject — basic publish stays open (Phase 1).
    client.table("shops").update({"trust_badges": badges}).eq("id", shop_id).execute()

    try:
        from common.events.publishers import publish_shop_verification_changed
        await publish_shop_verification_changed(
            shop_id=shop_id,
            new_status="rejected",
            stage=stage,
            reason=notes,
        )
    except Exception as exc:
        logger.warning("publish verification rejection event failed for shop %s: %s", shop_id, exc)

    try:
        await _send_stage_decision_email(shop_id, stage, "rejected", notes)
    except Exception:
        logger.exception("reject decision email failed")

    result = upd.data[0] if upd.data else row
    return _row_to_response(result).model_dump()
