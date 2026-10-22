import hmac
import os

from fastapi import HTTPException, Request


async def verify_cron_secret(request: Request) -> bool:
    """Require the configured cron bearer token; missing or blank secrets deny access."""
    secret = os.environ.get("CRON_SECRET")
    auth_header = request.headers.get("authorization", "")
    if (
        not secret
        or not secret.strip()
        or not hmac.compare_digest(auth_header.encode(), f"Bearer {secret}".encode())
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True
