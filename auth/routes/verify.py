from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/verify-email")
async def verify_email_endpoint(token: str):
    from auth.providers import verify_email as do_verify
    try:
        do_verify(token)
        return {"message": "Email verified"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
