"""Shared error-logging helpers for Sernia AI tools."""

import asyncio
from typing import Any, Coroutine

import logfire
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, ToolRetryError
from pydantic_ai.toolsets import WrapperToolset

# PydanticAI control-flow exceptions that must propagate — never catch these.
_PASSTHROUGH_EXCEPTIONS = (ApprovalRequired, CallDeferred, ModelRetry, ToolRetryError)


def log_tool_error(
    tool_name: str,
    error: Exception,
    *,
    conversation_id: str = "",
) -> None:
    """Log a tool error with full stack trace and structured fields.

    Must be called inside an ``except`` block so ``logfire.exception()``
    can capture ``exc_info`` automatically.
    """
    logfire.exception(
        "sernia tool error: {tool_name}",
        tool_name=tool_name,
        error_type=type(error).__name__,
        error_message=str(error),
        conversation_id=conversation_id
    )


class ErrorLoggingToolset(WrapperToolset):
    """Safety-net wrapper: catches unhandled tool exceptions, logs them with
    structured fields + stack trace, and returns a friendly error string so
    the conversation continues.

    Tools that handle their own errors (returning a string) are transparent
    to this wrapper — the ``except`` here only fires for truly unhandled
    exceptions.  New tools get error logging for free without any per-tool
    boilerplate.

    PydanticAI control-flow exceptions (ApprovalRequired, ModelRetry, etc.)
    are always re-raised so the framework can handle them.

    The optional ``name`` kwarg labels the toolset for admin/debug surfaces
    (e.g. the Context tab). Pydantic-ai's stock ``label`` property bakes in
    the wrapper class chain, which isn't useful for humans.
    """

    def __init__(self, wrapped, *, name: str | None = None):
        super().__init__(wrapped)
        self.name = name

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext,
        tool: Any,
    ) -> Any:
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except _PASSTHROUGH_EXCEPTIONS:
            raise
        except Exception as e:
            conversation_id = getattr(ctx.deps, "conversation_id", "")
            log_tool_error(name, e, conversation_id=conversation_id)
            return f"Error in {name}: {e}"


def create_logged_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Create a fire-and-forget task with error logging to Logfire.

    Unlike plain asyncio.create_task(), exceptions in the coroutine are logged
    immediately via logfire.error() instead of being silently swallowed until
    the task is awaited (which never happens for fire-and-forget tasks).

    Usage:
        create_logged_task(commit_and_push(path), name="git_sync")
        create_logged_task(notify_pending_approval(...), name="push_notification")
    """
    task = asyncio.create_task(coro, name=name)

    def _on_done(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logfire.error(
                "background task failed: {task_name}",
                task_name=name or t.get_name(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    task.add_done_callback(_on_done)
    return task
