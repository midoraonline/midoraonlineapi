from fastapi import APIRouter, HTTPException, Query

from auth.providers import verify_email as do_verify

router = APIRouter()


@router.api_route("/verify-email", methods=["GET", "POST"])
async def verify_email_endpoint(token: str = Query(...)):
    try:
        result = do_verify(token)
        # Return user details and fresh tokens
        return {
            "message": "Email verified",
            "user": result["user"],
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": "bearer",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
