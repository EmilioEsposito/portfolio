"""Refresh tokens encrypted at rest, with the key in ``SHOWING_TOKEN_KEY``.

A Google refresh token lets the app read free/busy times and manage the
showings calendar for as long as the grant lasts, so it is never stored in
the clear. ``calendar_connections.encrypted_refresh_token`` holds it encrypted
with Fernet (AES-128-CBC with an HMAC-SHA256 check), keyed by
``SHOWING_TOKEN_KEY`` and read at call time.

Each environment that connects calendars has its own key, and previews have
none. A preview's database is a copy of production's rows: without
production's key the copied token cannot be read, so the connection is
unusable there. Make a key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os

from cryptography.fernet import Fernet, InvalidToken

TOKEN_KEY_ENV = "SHOWING_TOKEN_KEY"


class TokenUnreadable(Exception):
    """A token cannot be encrypted or read here.

    ``reason`` is "no_key" (the variable is not set), "bad_key" (it is not a
    Fernet key) or "wrong_key" (the token was saved with another key).
    """

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _fernet() -> Fernet:
    key = os.getenv(TOKEN_KEY_ENV, "").strip()
    if not key:
        raise TokenUnreadable("no_key", f"{TOKEN_KEY_ENV} is not set here.")
    try:
        return Fernet(key)
    except ValueError:
        raise TokenUnreadable("bad_key", f"{TOKEN_KEY_ENV} is not a valid Fernet key.") from None


def key_problem() -> TokenUnreadable | None:
    """Why tokens cannot be saved or read here, or None when the key is usable."""
    try:
        _fernet()
    except TokenUnreadable as problem:
        return problem
    return None


def encrypt(token: str) -> str:
    """The token encrypted with this environment's key. Raises ``TokenUnreadable``."""
    return _fernet().encrypt(token.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """The token saved by ``encrypt``. Raises ``TokenUnreadable``, never quoting the token."""
    fernet = _fernet()
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise TokenUnreadable(
            "wrong_key", "The stored token was saved with another key, so it cannot be read here."
        ) from None
