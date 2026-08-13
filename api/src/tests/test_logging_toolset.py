"""
Unit tests for ErrorLoggingToolset log-level routing and repeat-loop guidance.

The wrapper catches unhandled tool exceptions and returns a friendly error
string so the conversation continues. Expected, model-recoverable errors
(sandbox file-tool errors) must log at WARNING level so they don't trip the
error-level Logfire alert, while genuinely unexpected failures stay at ERROR.

It also counts identical recoverable failures per run and appends a "STOP,
change approach" instruction once a call has failed the same way twice — the
loop that exhausted a scheduled run's request limit on 2026-08-08.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai_filesystem_sandbox import EditError, PathNotInSandboxError

from api.src.sernia_ai.tools._logging import _EDIT_RECOVERY_HINT, ErrorLoggingToolset


class _FakeToolset:
    """Minimal wrapped toolset whose call_tool raises a configured exception."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def call_tool(self, name, tool_args, ctx, tool):
        raise self._exc


def _make_ctx(conversation_id: str = "conv-1", *, track_repeats: bool = False):
    """Fake RunContext.

    ``track_repeats`` gives deps the real ``recoverable_tool_error_counts``
    dict that ``SerniaDeps`` carries. Without it (the default) deps is a bare
    MagicMock, mirroring a deps object that lacks the attribute — repeat
    counting must then stay inert.
    """
    ctx = MagicMock()
    ctx.deps.conversation_id = conversation_id
    ctx.deps.recoverable_tool_error_counts = {} if track_repeats else MagicMock()
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
async def test_edit_error_tells_model_to_reread_the_file():
    """An EditError carries the re-read hint that breaks the blind-retry loop.

    Without it the model answers "text not found" by retrying the same edit
    with different indentation, which has exhausted a run's request budget
    (pydantic-ai's UsageLimitExceeded) instead of failing one tool call.
    """
    exc = EditError("/workspace/MEMORY.md", "text not found", "  - 2026-07-31 | SMS to …")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    with patch("api.src.sernia_ai.tools._logging.logfire"):
        result = await ts.call_tool("workspace_edit_file", {}, _make_ctx(), MagicMock())

    assert result.endswith(_EDIT_RECOVERY_HINT)
    assert "workspace_read_file" in result
    # The original sandbox message survives ahead of the hint.
    assert "text not found" in result


@pytest.mark.asyncio
async def test_non_edit_sandbox_error_has_no_edit_hint():
    """The re-read hint is EditError-specific — a bad path doesn't get it."""
    ts = ErrorLoggingToolset(_FakeToolset(PathNotInSandboxError("/etc/passwd", ["/workspace"])))
    with patch("api.src.sernia_ai.tools._logging.logfire"):
        result = await ts.call_tool("workspace_read_file", {}, _make_ctx(), MagicMock())

    assert _EDIT_RECOVERY_HINT not in result


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
async def test_identical_recoverable_failure_escalates_to_stop_guidance():
    """A second byte-identical failing call gets "STOP" + recovery steps.

    This is the loop-breaker: the first failure returns the bare error (state
    can legitimately change between calls), the repeat tells the model to stop
    re-sending the same arguments.
    """
    exc = EditError("/workspace/MEMORY.md", "text not found in file", "  - 2026-07-31 | SMS")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    ctx = _make_ctx(track_repeats=True)
    args = {"path": "/workspace/MEMORY.md", "old_text": "  - 2026-07-31 | SMS", "new_text": ""}

    with patch("api.src.sernia_ai.tools._logging.logfire"):
        first = await ts.call_tool("workspace_edit_file", args, ctx, MagicMock())
        second = await ts.call_tool("workspace_edit_file", dict(args), ctx, MagicMock())

    assert "STOP" not in first
    assert "STOP" in second
    # Both still carry the underlying error so the model knows what failed.
    assert "text not found in file" in first
    assert "text not found in file" in second


@pytest.mark.asyncio
async def test_different_arguments_do_not_escalate():
    """Retrying with *changed* arguments is legitimate — no STOP guidance."""
    exc = EditError("/workspace/MEMORY.md", "text not found in file", "x")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    ctx = _make_ctx(track_repeats=True)

    with patch("api.src.sernia_ai.tools._logging.logfire"):
        first = await ts.call_tool("workspace_edit_file", {"old_text": "  - a"}, ctx, MagicMock())
        second = await ts.call_tool("workspace_edit_file", {"old_text": "   - a"}, ctx, MagicMock())

    assert "STOP" not in first
    assert "STOP" not in second


@pytest.mark.asyncio
async def test_repeat_counts_are_scoped_to_one_run():
    """Counts live on deps, so a fresh run starts clean."""
    exc = EditError("/workspace/MEMORY.md", "text not found in file", "x")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    args = {"old_text": "  - a"}

    with patch("api.src.sernia_ai.tools._logging.logfire"):
        ctx_run_1 = _make_ctx(track_repeats=True)
        await ts.call_tool("workspace_edit_file", args, ctx_run_1, MagicMock())
        ctx_run_2 = _make_ctx(track_repeats=True)
        first_of_run_2 = await ts.call_tool(
            "workspace_edit_file", dict(args), ctx_run_2, MagicMock()
        )

    assert "STOP" not in first_of_run_2


@pytest.mark.asyncio
async def test_deps_without_counter_never_escalates():
    """Deps lacking the attribute degrade to the old behavior, not a crash."""
    exc = EditError("/workspace/MEMORY.md", "text not found in file", "x")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    ctx = _make_ctx()  # deps.recoverable_tool_error_counts is a MagicMock, not a dict

    with patch("api.src.sernia_ai.tools._logging.logfire"):
        for _ in range(4):
            result = await ts.call_tool("workspace_edit_file", {"old_text": "a"}, ctx, MagicMock())

    assert "STOP" not in result


@pytest.mark.asyncio
async def test_non_json_arguments_still_fingerprint_consistently():
    """Arguments JSON can't encode natively still compare equal across calls."""
    exc = EditError("/workspace/MEMORY.md", "text not found in file", "x")
    ts = ErrorLoggingToolset(_FakeToolset(exc), name="workspace")
    ctx = _make_ctx(track_repeats=True)
    args = {"old_text": {1, 2, 3}}  # a set is not JSON-serializable

    with patch("api.src.sernia_ai.tools._logging.logfire"):
        first = await ts.call_tool("workspace_edit_file", args, ctx, MagicMock())
        second = await ts.call_tool("workspace_edit_file", args, ctx, MagicMock())

    assert "STOP" not in first
    assert "STOP" in second


@pytest.mark.asyncio
async def test_control_flow_exception_propagates_unlogged():
    """PydanticAI control-flow exceptions re-raise and are never logged."""
    ts = ErrorLoggingToolset(_FakeToolset(ModelRetry("retry with better args")), name="x")
    with patch("api.src.sernia_ai.tools._logging.logfire") as lf:
        with pytest.raises(ModelRetry):
            await ts.call_tool("some_tool", {}, _make_ctx(), MagicMock())

    lf.warn.assert_not_called()
    lf.exception.assert_not_called()
