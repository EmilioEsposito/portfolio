"""
Tests for admin password verification.

Moved verbatim from ``api/src/utils/password.py``.
"""

import pytest

from api.src.utils.password import verify_admin_password


@pytest.mark.asyncio
async def test_verify_admin_password():
    assert not await verify_admin_password("wrong123")
    assert not await verify_admin_password("wrong456")
