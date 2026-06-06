"""
Unit tests for ErrorLoggingToolset log-level routing.

The wrapper catches unhandled tool exceptions and returns a friendly error
string so the conversation continues. Expected, model-recoverable errors
(sandbox file-tool errors) must log at WARNING level so they don't trip the
error-level Logfire alert, while genuinely unexpected failures stay at ERROR.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai_filesystem_sandbox import EditError, PathNotInSandboxError

from api.src.sernia_ai.tools._logging import ErrorLoggingToolset


class _FakeToolset:
    """Minimal wrapped toolset whose call_tool raises a configured exception."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def call_tool(self, name, tool_args, ctx, tool):
        raise self._exc


def _make_ctx(conversation_id: str = "conv-1"):
    ctx = MagicMock()
    ctx.deps.conversation_id = conversation_id
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        EditError("/workspace/MEMORY.md", "text not found", "| Jeri Frizza | 320-02 |"),
        PathNotInSandboxError("/etc/passwd", ["/workspace"]),
    ],
)
async def test_recoverable_sandbox_error_logged_as_warning(exc):
    """Sandbox file-tool errors log at warn (not error) and return a string."""
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    with patch("api.src.sernia_ai.tools._logging.logfire") as lf:
        result = await ts.call_tool("workspace_edit_file", {}, _make_ctx(), MagicMock())

    lf.warn.assert_called_once()
    lf.exception.assert_not_called()
    assert "Error in workspace_edit_file" in result


@pytest.mark.asyncio
async def test_unexpected_error_logged_as_error():
    """Non-recoverable exceptions stay at error level (trips the alert)."""
    ts = ErrorLoggingToolset(_FakeToolset(RuntimeError("DB connection lost")), name="db")
    with patch("api.src.sernia_ai.tools._logging.logfire") as lf:
        result = await ts.call_tool("db_search_sms_history", {}, _make_ctx(), MagicMock())

    lf.exception.assert_called_once()
    lf.warn.assert_not_called()
    assert "Error in db_search_sms_history" in result


@pytest.mark.asyncio
async def test_control_flow_exception_propagates_unlogged():
    """PydanticAI control-flow exceptions re-raise and are never logged."""
    ts = ErrorLoggingToolset(_FakeToolset(ModelRetry("retry with better args")), name="x")
    with patch("api.src.sernia_ai.tools._logging.logfire") as lf:
        with pytest.raises(ModelRetry):
            await ts.call_tool("some_tool", {}, _make_ctx(), MagicMock())

    lf.warn.assert_not_called()
    lf.exception.assert_not_called()
