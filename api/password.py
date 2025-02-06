import os
import hmac
import json
from fastapi import HTTPException, Request

def adhoc_generate_new_password():
    import hashlib
    import secrets

    # Generate a random salt
    salt = secrets.token_hex(16)
    password = input("Type password and hit ENTER: ")

    # Create salted hash
    password_bytes = (password + salt).encode('utf-8')
    password_hash = hashlib.sha256(password_bytes).hexdigest()

    print(f"Add these to your .env file:")
    print(f"NEXT_PUBLIC_ADMIN_PASSWORD_SALT={salt}")
    print(f"BUILDING_MESSAGE_PASSWORD_HASH={password_hash}")



def verify_admin_password_hash(password_hash: str) -> bool:
    """
    Verify the admin password hash.
    Returns True if valid, raises HTTPException if invalid.
    """
    correct_hash = os.getenv("ADMIN_PASSWORD_HASH")
    
    if not correct_hash:
        raise HTTPException(500, "Password hash not configured")
    
    if not hmac.compare_digest(password_hash, correct_hash):
        raise HTTPException(401, "Invalid password")
    
    return True

async def verify_admin_auth(request: Request):
    """Dependency function to verify admin password"""
    try:
        body = await request.json()
        password_hash = body.get("password_hash")
        if not password_hash:
            raise HTTPException(401, "password_hash required")
        return verify_admin_password_hash(password_hash)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")