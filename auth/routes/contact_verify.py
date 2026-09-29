from fastapi import APIRouter, Depends, HTTPException

from auth.contact import contact_verification_status
from auth.phone import PhoneError, normalize_phone
from auth.schemas import (
    EmailOtpConfirmRequest,
    EmailOtpStartRequest,
    MessageResponse,
    PhoneOtpConfirmRequest,
    PhoneOtpStartRequest,
    VerificationStatusResponse,
)
from common.verification_service import VerificationError, http_status_for
from core.security import get_current_user_id
from db.supabase import get_supabase_admin

router = APIRouter()


def _raise_verification(exc: VerificationError) -> None:
    raise HTTPException(
        status_code=http_status_for(exc.code),
        detail={"detail": str(exc), "code": exc.code},
    )


def _user_row(user_id: str) -> dict:
    row = (
        get_supabase_admin()
        .table("users")
        .select("id,email,email_verified,phone_number,phone_verified")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail={"detail": "User not found", "code": "user_not_found"})
    return row.data[0]


def _status(user_id: str) -> VerificationStatusResponse:
    return VerificationStatusResponse.model_validate(contact_verification_status(_user_row(user_id)))


@router.get("/verification-status", response_model=VerificationStatusResponse)
async def verification_status(user_id: str = Depends(get_current_user_id)) -> VerificationStatusResponse:
    return _status(user_id)


@router.post("/verify/phone/start", response_model=MessageResponse)
async def start_phone_verification(
    body: PhoneOtpStartRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import assert_phone_available
    from common.verification_service import send_verification_code

    try:
        phone = normalize_phone(body.phone)
    except PhoneError as exc:
        _raise_verification(VerificationError(str(exc), exc.code))
    try:
        assert_phone_available(phone, user_id)
    except ValueError:
        _raise_verification(
            VerificationError(
                "This phone number is already linked to another account.",
                "phone_in_use",
            )
        )
    try:
        send_verification_code(
            user_id=user_id,
            phone_number=phone,
            purpose="phone",
            channel="sms",
            target_type="user",
            target_id=user_id,
        )
    except VerificationError as exc:
        _raise_verification(exc)
    return MessageResponse(message="Verification code sent")


@router.post("/verify/phone/confirm", response_model=VerificationStatusResponse)
async def confirm_phone_verification(
    body: PhoneOtpConfirmRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import assert_phone_available
    from common.verification_service import confirm_verification_code

    try:
        phone = normalize_phone(body.phone)
    except PhoneError as exc:
        _raise_verification(VerificationError(str(exc), exc.code))
    try:
        assert_phone_available(phone, user_id)
    except ValueError:
        _raise_verification(
            VerificationError(
                "This phone number is already linked to another account.",
                "phone_in_use",
            )
        )
    try:
        confirmed = confirm_verification_code(
            user_id=user_id,
            code=body.code,
            purpose="phone",
            target_type="user",
            target_id=user_id,
            contact=phone,
        )
    except VerificationError as exc:
        _raise_verification(exc)
    admin = get_supabase_admin()
    admin.table("users").update(
        {"phone_number": confirmed, "phone_verified": True}
    ).eq("id", user_id).execute()
    try:
        admin.table("profiles").update({"phone_number": confirmed}).eq("id", user_id).execute()
    except Exception:
        pass
    return _status(user_id)


@router.post("/verify/email/start", response_model=MessageResponse)
async def start_email_verification(
    body: EmailOtpStartRequest,
    user_id: str = Depends(get_current_user_id),
):
    from common.verification_service import issue_verification_code, revoke_latest_code
    from mail.send import send_email_otp

    account = _user_row(user_id)
    email = str(body.email).strip().lower()
    if email != str(account.get("email") or "").strip().lower():
        _raise_verification(
            VerificationError("Verify the email address on this account.", "invalid_email")
        )
    try:
        code = issue_verification_code(
            user_id=user_id,
            contact=email,
            purpose="email",
            channel="email",
            target_type="user",
            target_id=user_id,
        )
    except VerificationError as exc:
        _raise_verification(exc)
    try:
        await send_email_otp(email, code)
    except Exception as exc:
        revoke_latest_code(user_id=user_id, purpose="email", contact=email)
        raise HTTPException(
            status_code=400,
            detail={
                "detail": "The verification email could not be sent.",
                "code": "email_unavailable",
            },
        ) from exc
    return MessageResponse(message="Verification code sent")


@router.post("/verify/email/confirm", response_model=VerificationStatusResponse)
async def confirm_email_verification(
    body: EmailOtpConfirmRequest,
    user_id: str = Depends(get_current_user_id),
):
    from common.verification_service import confirm_verification_code

    account = _user_row(user_id)
    email = str(body.email).strip().lower()
    if email != str(account.get("email") or "").strip().lower():
        _raise_verification(
            VerificationError("Verify the email address on this account.", "invalid_email")
        )
    try:
        confirm_verification_code(
            user_id=user_id,
            code=body.code,
            purpose="email",
            target_type="user",
            target_id=user_id,
            contact=email,
        )
    except VerificationError as exc:
        _raise_verification(exc)
    get_supabase_admin().table("users").update({"email_verified": True}).eq("id", user_id).execute()
    return _status(user_id)
