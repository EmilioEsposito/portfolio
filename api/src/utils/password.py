from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env.development.local'))
import os
import hmac
import json
from fastapi import HTTPException, Request
import pytest

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
    print(f"ADMIN_PASSWORD_SALT={salt}")
    print(f"ADMIN_PASSWORD_HASH={password_hash}")

def hash_password(password: str) -> str:
    """Hash a plaintext password using the salt from environment variables."""
    import hashlib
    
    salt = os.getenv("ADMIN_PASSWORD_SALT")
    if not salt:
        raise HTTPException(500, "Password salt not configured")
    
    # Create salted hash
    password_bytes = (password + salt).encode('utf-8')
    return hashlib.sha256(password_bytes).hexdigest()

async def verify_admin_password(password_attempt: str) -> bool:
    password_attempt_hash = hash_password(password_attempt)
    correct_hash = os.getenv("ADMIN_PASSWORD_HASH")
    
    if not correct_hash:
        raise HTTPException(500, "Password hash not configured")
    
    is_valid = hmac.compare_digest(password_attempt_hash, correct_hash)
    
    return is_valid

@pytest.mark.asyncio
async def test_verify_admin_password():
    assert await verify_admin_password("wrong123") == False
    assert await verify_admin_password("wrong456") == False


async def verify_admin_auth(request: Request) -> bool:
    """FastAPI dependency function to verify admin password from request body"""
    try:
        body = await request.json()
        password = body.get("password")
        if not password:
            raise HTTPException(401, "Password required")
            
        is_valid = await verify_admin_password(password)

        if is_valid:
            return True
        else:
            raise HTTPException(401, "Invalid password")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON body")