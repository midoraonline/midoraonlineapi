from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from auth.preferences import merge_preferences, load_preferences
from auth.schemas import (
    ChangePasswordRequest,
    MessageResponse,
    ProfileResponse,
    UpdateProfileRequest,
    profile_response,
)
from core.security import get_current_user_id

router = APIRouter()


def _profile_http(profile: dict, user_id: str) -> ProfileResponse:
    from auth.service import create_supabase_realtime_jwt

    return profile_response(profile, realtime_token=create_supabase_realtime_jwt(user_id))


def _direct_avatar_upload() -> HTTPException:
    # 503 matches the Settings client, which falls back to UploadThing + PATCH
    # on 404, 405, and 503. 413 from this app would be shown as a hard failure.
    return HTTPException(
        status_code=503,
        detail={
            "detail": "Upload this photo directly, then save the URL. The API cannot accept files this large.",
            "code": "avatar_direct_upload_required",
            "upload_token": "POST /api/v1/auth/upload-token",
            "save": "PATCH /api/v1/auth/me",
            "body": {"avatar_url": "https://"},
        },
    )


def _value_error(exc: ValueError) -> HTTPException:
    msg = str(exc)
    if "already linked" in msg.lower():
        return HTTPException(status_code=409, detail={"detail": msg, "code": "phone_taken"})
    if "migration 049" in msg:
        return HTTPException(status_code=503, detail={"detail": msg, "code": "migration_required"})
    return HTTPException(status_code=400, detail=msg)


@router.patch("/me", response_model=ProfileResponse)
async def update_me(
    body: UpdateProfileRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import update_profile

    preferences = None
    update_preferences = "preferences" in body.model_fields_set and body.preferences is not None
    if update_preferences and body.preferences is not None:
        preferences = merge_preferences(load_preferences(user_id), body.preferences)
    try:
        profile = update_profile(
            user_id,
            body.full_name,
            body.phone_number,
            body.avatar_url,
            update_avatar="avatar_url" in body.model_fields_set,
            bio=body.bio,
            update_bio="bio" in body.model_fields_set,
            preferences=preferences,
            update_preferences=update_preferences,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    return _profile_http(profile, user_id)


@router.post("/me/avatar", response_model=ProfileResponse)
async def upload_avatar(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    file: UploadFile = File(...),
):
    """Store a profile photo.

    Prefer a direct UploadThing upload from the browser, then
    PATCH /api/v1/auth/me {"avatar_url": "https://..."}. This multipart
    route stays for small files. Vercel rejects bodies over about 4.5 MB
    before this code runs. JPEG, PNG, WebP, and GIF are stored unchanged.
    HEIC/HEIF is converted to JPEG at quality 100 and the original pixel size.
    """
    from auth.providers.emailpassword import get_profile, update_profile
    from media.avatar_image import AvatarImageError, prepare_avatar_upload
    from media.cleanup import cleanup_removed_media
    from media.uploadthing import upload_file_bytes

    raw_len = request.headers.get("content-length")
    if raw_len and raw_len.isdigit() and int(raw_len) > 4 * 1024 * 1024:
        raise _direct_avatar_upload()

    data = await file.read()
    if len(data) > 4 * 1024 * 1024:
        raise _direct_avatar_upload()
    try:
        payload, content_type, filename = prepare_avatar_upload(
            data,
            content_type=file.content_type,
            filename=file.filename,
        )
    except AvatarImageError as exc:
        status = 413 if exc.code == "avatar_too_large" else 415
        if exc.code == "heic_converter_unavailable":
            status = 503
        raise HTTPException(status_code=status, detail={"detail": str(exc), "code": exc.code}) from exc
    try:
        url = await upload_file_bytes(payload, filename=filename, content_type=content_type)
    except RuntimeError as exc:
        code = str(exc)
        status = 503 if code == "uploadthing_unconfigured" else 502
        raise HTTPException(
            status_code=status,
            detail={"detail": "Profile photo upload failed.", "code": code},
        ) from exc
    previous = get_profile(user_id) or {}
    try:
        profile = update_profile(
            user_id,
            None,
            None,
            url,
            update_avatar=True,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    old_url = previous.get("avatar_url")
    if old_url and old_url != url:
        await cleanup_removed_media([old_url], [url])
    return _profile_http(profile, user_id)


@router.post("/change-password", response_model=MessageResponse)
async def change_password_endpoint(
    body: ChangePasswordRequest,
    user_id: str = Depends(get_current_user_id),
):
    from auth.providers.emailpassword import change_password

    try:
        change_password(user_id, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return MessageResponse(message="Password changed successfully")
