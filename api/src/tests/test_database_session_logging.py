"""The DB session helpers must not log expected HTTPExceptions as errors."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.src.database import database


def _fake_factory() -> tuple[MagicMock, AsyncMock]:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, session


@pytest.mark.asyncio
async def test_get_session_rolls_back_http_exception_without_error_log() -> None:
    factory, session = _fake_factory()
    with (
        patch.object(database, "AsyncSessionFactory", factory),
        patch.object(database.logfire, "exception") as log_exception,
    ):
        gen = database.get_session()
        await gen.__anext__()
        with pytest.raises(HTTPException):
            await gen.athrow(HTTPException(status_code=422, detail="Not saved."))
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    log_exception.assert_not_called()


@pytest.mark.asyncio
async def test_get_session_still_logs_unexpected_errors() -> None:
    factory, session = _fake_factory()
    with (
        patch.object(database, "AsyncSessionFactory", factory),
        patch.object(database.logfire, "exception") as log_exception,
    ):
        gen = database.get_session()
        await gen.__anext__()
        with pytest.raises(RuntimeError):
            await gen.athrow(RuntimeError("boom"))
    session.rollback.assert_awaited_once()
    log_exception.assert_called_once()


@pytest.mark.asyncio
async def test_session_context_rolls_back_http_exception_without_error_log() -> None:
    factory, session = _fake_factory()
    with (
        patch.object(database, "AsyncSessionFactory", factory),
        patch.object(database.logfire, "exception") as log_exception,
    ):
        with pytest.raises(HTTPException):
            async with database.session_context():
                raise HTTPException(status_code=502, detail="upstream refused")
    session.rollback.assert_awaited_once()
    log_exception.assert_not_called()
