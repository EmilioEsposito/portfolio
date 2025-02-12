from fastapi import Request, HTTPException
import os
from api_src.utils.password import verify_admin_auth

async def verify_cron_secret(request: Request):
    """
    Dependency to verify the cron job secret token.
    Raises 401 if unauthorized.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or auth_header != f"Bearer {os.environ.get('CRON_SECRET')}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True 

async def verify_cron_or_admin(request: Request):
    """
    Dependency to verify either cron secret or admin authentication.
    Raises 401 if neither auth method succeeds.
    """
    try:
        # Try cron auth first
        await verify_cron_secret(request)
        return True
    except HTTPException:
        try:
            # If cron auth fails, try admin auth
            await verify_admin_auth(request)
            return True
        except HTTPException:
            # If both fail, raise unauthorized
            raise HTTPException(status_code=401, detail="Unauthorized: Requires either cron secret or admin authentication") 