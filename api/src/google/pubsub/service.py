"""
Service functionality for Google Pub/Sub operations.
"""

import logging
import time
import json
import requests
from fastapi import HTTPException
from google.auth import jwt
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Cache for Google's public keys
_GOOGLE_PUBLIC_KEYS = None
_GOOGLE_PUBLIC_KEYS_EXPIRY = 0

def get_google_public_keys():
    """
    Fetches and caches Google's public keys used for JWT verification.
    Keys are cached until their expiry time.
    """
    global _GOOGLE_PUBLIC_KEYS, _GOOGLE_PUBLIC_KEYS_EXPIRY
    
    # Return cached keys if they're still valid
    if _GOOGLE_PUBLIC_KEYS and time.time() < _GOOGLE_PUBLIC_KEYS_EXPIRY:
        return _GOOGLE_PUBLIC_KEYS
    
    # Fetch new keys
    resp = requests.get('https://www.googleapis.com/oauth2/v1/certs')
    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch Google public keys: {resp.status_code}")
    
    # Cache the keys and their expiry time
    _GOOGLE_PUBLIC_KEYS = resp.json()
    
    # Get cache expiry from headers (with some buffer time)
    cache_control = resp.headers.get('Cache-Control', '')
    if 'max-age=' in cache_control:
        max_age = int(cache_control.split('max-age=')[1].split(',')[0])
        _GOOGLE_PUBLIC_KEYS_EXPIRY = time.time() + max_age - 60  # 1 minute buffer
    else:
        _GOOGLE_PUBLIC_KEYS_EXPIRY = time.time() + 3600  # 1 hour default
    
    return _GOOGLE_PUBLIC_KEYS

async def verify_pubsub_token(auth_header: str, expected_audience: str) -> bool:
    """
    Verifies the Google Pub/Sub authentication token.
    
    Args:
        auth_header: The full authorization header
        expected_audience: The expected audience claim in the token
        
    Returns:
        True if token is valid
        
    Raises:
        HTTPException: If verification fails
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.error(f"Invalid auth header: {auth_header}")
        raise HTTPException(status_code=401, detail="Invalid or missing authorization token")
    
    try:
        # Extract token
        token = auth_header.split("Bearer ")[1]
        logger.info(f"Verifying token: {token[:20]}...")
        logger.info(f"Expected audience: {expected_audience}")
        
        # Get Google's public keys
        certs = get_google_public_keys()
        
        # Verify token signature and claims using jwt.decode
        claims = jwt.decode(token, certs=certs)
        logger.info(f"Token claims: {json.dumps(claims, indent=2)}")
        
        # Verify audience
        token_audience = claims.get('aud').split('?')[0]  # ignore query params
        if token_audience != expected_audience:
            logger.error(f"Invalid audience. Expected {expected_audience}, got {token_audience}")
            raise HTTPException(status_code=401, detail="Invalid token audience")
        
        # Verify issuer
        if claims.get('iss') != 'https://accounts.google.com':
            logger.error(f"Invalid issuer: {claims.get('iss')}")
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        
        # Verify service account email
        email = claims.get('email', '')
        if not email.endswith('gserviceaccount.com'):
            logger.error(f"Invalid service account email: {email}")
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        
        return True
        
    except ValueError as e:
        logger.error(f"Token validation error: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Invalid token format: {str(e)}")
    except Exception as e:
        logger.error(f"Pub/Sub token verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")

def decode_pubsub_message(message_data: str) -> Dict[str, Any]:
    """
    Decodes a base64-encoded Pub/Sub message.
    
    Args:
        message_data: Base64-encoded message data
        
    Returns:
        Decoded message as dictionary
        
    Raises:
        HTTPException: If decoding fails
    """
    import base64
    try:
        # Decode base64 message data
        decoded_bytes = base64.b64decode(message_data)
        decoded_json = json.loads(decoded_bytes.decode('utf-8'))
        logger.info(f"Decoded Pub/Sub message: {json.dumps(decoded_json, indent=2)}")
        return decoded_json
        
    except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to decode message data: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to decode message data: {str(e)}"
        ) 