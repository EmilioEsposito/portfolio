"""Generate VAPID keypair for Web Push notifications.

Usage:
    source .venv/bin/activate && python adhoc/create_push_keys.py

Copy the output directly into your .env file.
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

private_key = ec.generate_private_key(ec.SECP256R1())

# PEM private key (for pywebpush vapid_private_key param)
private_pem = private_key.private_bytes(
    encoding=Encoding.PEM,
    format=PrivateFormat.PKCS8,
    encryption_algorithm=NoEncryption(),
).decode().strip()

# URL-safe base64 public key (for browser applicationServerKey)
public_raw = private_key.public_key().public_bytes(
    encoding=Encoding.X962,
    format=PublicFormat.UncompressedPoint,
)
public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode()

print("# --- Copy below into .env and Railway env vars ---\n")
print(f'VAPID_PRIVATE_KEY="{private_pem}"')
print(f"VAPID_PUBLIC_KEY={public_b64}")
print("VAPID_CLAIM_EMAIL=mailto:admin@serniacapital.com")
