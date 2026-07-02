"""
Database connectivity smoke tests.

Thin pytest wrappers around the runtime health checks in
``api.src.database.database`` (``check_*`` functions), which are also
called at app startup via ``wait_for_db()`` / ``api/index.py``.
"""

import pytest

from api.src.database.database import (
    check_async_engine_select_one,
    check_database_connections,
    check_sync_engine_select_one,
)


def test_sync_engine_select_one():
    """SELECT 1 via the synchronous engine."""
    check_sync_engine_select_one()


@pytest.mark.asyncio
async def test_async_engine_select_one():
    """SELECT 1 via the async engine."""
    await check_async_engine_select_one()


@pytest.mark.asyncio
async def test_database_connections():
    """Combined sync + async engine connectivity check."""
    await check_database_connections()
