"""Shop verification endpoints (merchant + admin).

Flow:
    1. A merchant submits their shop for verification (`POST /verifications/submit`).
       This creates / resets a row in `shop_verifications` with status=pending.
    2. An admin lists pending verifications, then either approves or rejects.
    3. Approving flips `shops.is_active = true` and notifies the merchant by
       email (best-effort).

The `shop_verifications` table has a unique index on `shop_id` so we upsert
rather than insert multiple rows per shop.
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


def _row_to_response(row: dict[str, Any]) -> VerificationResponse:
    return VerificationResponse(
        id=str(row.get("id", "")),
        shop_id=str(row.get("shop_id", "")),
        status=row.get("status") or "unverified",
        requested_at=str(row["requested_at"]) if row.get("requested_at") else None,
        reviewed_at=str(row["reviewed_at"]) if row.get("reviewed_at") else None,
        reviewed_by=str(row["reviewed_by"]) if row.get("reviewed_by") else None,
        notes=row.get("notes"),
        metadata=row.get("metadata"),
        submitted_docs=row.get("submitted_docs"),
        submitted_phone=row.get("submitted_phone"),
        submitted_whatsapp=row.get("submitted_whatsapp"),
        submitted_location=row.get("submitted_location"),
        shop_duration_days=row.get("shop_duration_days") or 0,
    )


def _upsert_verification(
    shop_id: str,
    *,
    status: str,
    notes: str | None = None,
    reviewed_by: str | None = None,
    metadata: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    submitted_phone: str | None = None,
    submitted_whatsapp: str | None = None,
    submitted_location: str | None = None,
) -> dict[str, Any]:
    client = get_supabase_admin()
    now_iso = datetime.now(timezone.utc).isoformat()

    payload: dict[str, Any] = {
        "shop_id": shop_id,
        "status": status,
        "notes": notes,
        "metadata": metadata,
    }
    if documents is not None:
        payload["submitted_docs"] = documents
    if submitted_phone is not None:
        payload["submitted_phone"] = submitted_phone
    if submitted_whatsapp is not None:
        payload["submitted_whatsapp"] = submitted_whatsapp
    if submitted_location is not None:
        payload["submitted_location"] = submitted_location

    if status == "pending":
        payload["requested_at"] = now_iso
        payload["reviewed_at"] = None
        payload["reviewed_by"] = None
        try:
            shop_r = client.table("shops").select("created_at").eq("id", shop_id).execute()
            if shop_r.data:
                created = shop_r.data[0].get("created_at")
                if created:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))
                    payload["shop_duration_days"] = delta.days
        except Exception:
            pass
    else:
        payload["reviewed_at"] = now_iso
        payload["reviewed_by"] = reviewed_by

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
        r = client.table("shop_verifications").insert(payload).execute()
    if not r.data:
        raise HTTPException(status_code=500, detail="Failed to persist verification")
    return r.data[0]


def _resolve_merchant_email(client: Any, shop: dict[str, Any]) -> str | None:
    to_email = shop.get("shop_email")
    if to_email:
        return to_email
    owner_id = shop.get("owner_id")
    if not owner_id:
        return None
    u = (
        client.table("users")
        .select("email")
        .eq("id", owner_id)
        .limit(1)
        .execute()
    )
    if u.data:
        return u.data[0].get("email")
    return None


async def _send_verification_email(
    shop_id: str, decision: str, notes: str | None = None
) -> None:
    """Best-effort async email notification to the shop owner."""
    try:
        client = get_supabase_admin()
        r = (
            client.table("shops")
            .select("name, shop_email, owner_id")
            .eq("id", shop_id)
            .limit(1)
            .execute()
        )
        if not r.data:
            return
        shop = r.data[0]
        to_email = _resolve_merchant_email(client, shop)
        if not to_email:
            return

        from mail.send import send_shop_verification_decision_email

        await send_shop_verification_decision_email(
            to_email, shop.get("name", ""), decision, notes
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("verification email failed for shop %s: %s", shop_id, exc)


async def _send_submission_emails(shop_id: str, notes: str | None = None) -> None:
    """Notify merchant (confirmation) + admins (new submission) best-effort."""
    try:
        from core.config import get_settings
        from mail.send import (
            send_new_shop_submission_admin_email,
            send_shop_submission_received_email,
        )

        client = get_supabase_admin()
        r = (
            client.table("shops")
            .select("id, name, slug, shop_email, owner_id")
            .eq("id", shop_id)
            .limit(1)
            .execute()
        )
        if not r.data:
            return
        shop = r.data[0]
        merchant_email = _resolve_merchant_email(client, shop)

        if merchant_email:
            try:
                await send_shop_submission_received_email(
                    merchant_email, shop.get("name", "")
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "submission confirmation email failed for shop %s: %s",
                    shop_id,
                    exc,
                )

        from mail.queue import get_admin_emails
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "admin submission email failed for shop %s: %s", shop_id, exc
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("submission email pipeline failed for shop %s: %s", shop_id, exc)


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

    r = (
        client.table("shop_verifications")
        .select("*")
        .eq("shop_id", shop_id)
        .limit(1)
        .execute()
    )
    if not r.data:
        return VerificationResponse(
            id="",
            shop_id=shop_id,
            status="unverified",
        )
    return _row_to_response(r.data[0])


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

    docs_dict = [d.model_dump() for d in (body.documents or [])] if body.documents else None

    row = _upsert_verification(
        shop_id,
        status="pending",
        notes=body.notes,
        metadata=body.metadata,
        documents=docs_dict,
        submitted_phone=body.submitted_phone,
        submitted_whatsapp=body.submitted_whatsapp,
        submitted_location=body.submitted_location,
    )

    await _send_submission_emails(shop_id, notes=body.notes)

    return _row_to_response(row)


# ---------------------------------------------------------------------------
# Admin-facing endpoints
# ---------------------------------------------------------------------------


admin_router = APIRouter()


@admin_router.get("/verifications")
async def admin_list_verifications(
    status: Annotated[str | None, Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_unverified: Annotated[bool, Query()] = False,
):
    """List shop verifications. Pass `include_unverified=true` to also surface
    shops that have never been submitted yet — useful when the admin wants to
    verify an existing shop that the merchant forgot to submit.
    """
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

    # Optionally fold in shops that don't have a verifications row yet.
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
                items.append(
                    {
                        "id": "",
                        "shop_id": s["id"],
                        "status": "unverified",
                        "requested_at": None,
                        "reviewed_at": None,
                        "reviewed_by": None,
                        "notes": None,
                        "metadata": None,
                        "shops": {
                            "name": s.get("name"),
                            "slug": s.get("slug"),
                            "owner_id": s.get("owner_id"),
                            "shop_email": s.get("shop_email"),
                            "is_active": s.get("is_active"),
                            "created_at": s.get("created_at"),
                        },
                    }
                )
        except Exception:  # noqa: BLE001
            logger.exception("include_unverified expansion failed")

    return {"items": items}


@admin_router.post("/verifications/{shop_id}/queue", response_model=VerificationResponse)
async def admin_queue_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    claims=Depends(get_optional_claims),
) -> VerificationResponse:
    """Admin-side shortcut: push a shop into the verification queue even if
    the merchant hasn't submitted it. Useful for back-filling existing shops.
    """
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None
    row = _upsert_verification(
        shop_id,
        status="pending",
        notes=notes,
        reviewed_by=reviewer_id,
    )
    # Best-effort notification so the merchant knows admins are reviewing.
    try:
        await _send_submission_emails(shop_id, notes=notes)
    except Exception:  # noqa: BLE001
        logger.exception("queue verification email pipeline failed")
    return _row_to_response(row)


@admin_router.post("/verifications/{shop_id}/approve", response_model=VerificationResponse)
async def admin_approve_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    claims=Depends(get_optional_claims),
) -> VerificationResponse:
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None

    row = _upsert_verification(
        shop_id,
        status="verified",
        notes=notes,
        reviewed_by=reviewer_id,
    )

    client = get_supabase_admin()
    update = (
        client.table("shops")
        .update({"is_active": True})
        .eq("id", shop_id)
        .execute()
    )
    # Approving a verification implicitly elevates the owner to merchant.
    if update.data:
        owner_id = update.data[0].get("owner_id")
        if owner_id:
            from auth import service as auth_service

            auth_service.promote_to_merchant(str(owner_id))

    await _send_verification_email(shop_id, "verified", notes)
    return _row_to_response(row)


@admin_router.post("/verifications/{shop_id}/reject", response_model=VerificationResponse)
async def admin_reject_verification(
    shop_id: str,
    body: VerificationDecisionRequest | None = None,
    claims=Depends(get_optional_claims),
) -> VerificationResponse:
    reviewer_id = getattr(claims, "sub", None) if claims else None
    notes = body.notes if body else None

    row = _upsert_verification(
        shop_id,
        status="rejected",
        notes=notes,
        reviewed_by=reviewer_id,
    )

    client = get_supabase_admin()
    client.table("shops").update({"is_active": False}).eq("id", shop_id).execute()

    await _send_verification_email(shop_id, "rejected", notes)
    return _row_to_response(row)
