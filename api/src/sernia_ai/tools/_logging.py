"""Shared error-logging helpers for Sernia AI tools."""

from typing import Any

import logfire
from pydantic_ai import RunContext
from pydantic_ai.toolsets import WrapperToolset


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
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext,
        tool: Any,
    ) -> Any:
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except Exception as e:
            conversation_id = getattr(ctx.deps, "conversation_id", "")
            log_tool_error(name, e, conversation_id=conversation_id)
            return f"Error in {name}: {e}"
