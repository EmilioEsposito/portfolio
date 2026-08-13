"""Shared error-logging helpers for Sernia AI tools."""

import asyncio
import hashlib
import json
from collections.abc import Coroutine
from typing import Any

import logfire
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, ToolRetryError
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai_filesystem_sandbox import EditError, SandboxError

# PydanticAI control-flow exceptions that must propagate — never catch these.
_PASSTHROUGH_EXCEPTIONS = (ApprovalRequired, CallDeferred, ModelRetry, ToolRetryError)

# Appended to every EditError returned to the model. A bare EditError only says
# the search text wasn't found, which the model tends to answer by retrying the
# same edit with slightly different indentation — a loop that has burned entire
# runs' request budgets (a scheduled run on 2026-08-08 spent 11 workspace_edit_file
# calls on MEMORY.md and died on pydantic-ai's 50-request limit). The one thing
# that actually breaks the loop is re-reading the file, so say so explicitly.
# The stale-snapshot note matters most for MEMORY.md, which is injected into the
# prompt once per run and goes out of date the moment the model edits it.
_EDIT_RECOVERY_HINT = (
    " → Do not retry with a re-indented or reworded guess. Call "
    "workspace_read_file on this path first and copy the exact current text "
    "(if this is MEMORY.md, the copy injected into your prompt is a snapshot "
    "from the start of this run and is stale once you have edited it), then "
    "make a single corrected edit."
)

# Expected, model-recoverable tool errors. These are caused by the model's tool
# *arguments* (e.g. an edit whose search text isn't in the file, a path outside
# the sandbox, an oversized write), not by a system fault. The model sees the
# returned error string and retries — exactly like a validation error. We log
# them at warning level so they stay visible in traces without tripping the
# error-level "Error-level records (non-local)" alert, which pages on genuine
# failures. SandboxError is the base of all pydantic_ai_filesystem_sandbox
# input/content errors (EditError, PathNotInSandboxError, FileTooLargeError, …).
_RECOVERABLE_EXCEPTIONS = (SandboxError,)

# How many identical failing calls (same tool, byte-identical arguments) it
# takes before the returned error string carries extra guidance telling the
# model to change approach.
#
# A model that re-sends identical arguments after an identical error is stuck:
# the plain error string gave it no new information, so the next attempt fails
# the same way, and each attempt spends one of the run's ~50 model requests.
# That is how a scheduled run died with UsageLimitExceeded on 2026-08-08 —
# seven `workspace_edit_file` EditErrors on MEMORY.md, four of them byte-for-
# byte identical. Escalating on the SECOND identical failure still leaves one
# free retry (state can legitimately change between calls) while cutting the
# loop short well before the request budget runs out.
REPEAT_ERROR_THRESHOLD = 2


def _call_fingerprint(tool_name: str, tool_args: dict[str, Any]) -> str:
    """Stable hash of a tool call's name + arguments."""
    try:
        payload = json.dumps(tool_args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_args)
    return hashlib.sha256(f"{tool_name}:{payload}".encode()).hexdigest()


def _count_identical_failure(ctx: RunContext, tool_name: str, tool_args: dict[str, Any]) -> int:
    """Record an identical failing call; return how many times it has failed.

    Returns 1 for the first failure of a given (tool, args) pair in this run.
    Counts live on ``SerniaDeps.recoverable_tool_error_counts`` so their scope
    is exactly one agent run — a module-level cache would leak across runs
    (and across tests). Deps objects without the attribute never escalate.
    """
    counts = getattr(ctx.deps, "recoverable_tool_error_counts", None)
    if not isinstance(counts, dict):
        return 1
    key = _call_fingerprint(tool_name, tool_args)
    counts[key] = counts.get(key, 0) + 1
    return counts[key]


def _repeat_guidance(tool_name: str, attempts: int) -> str:
    """Extra instruction appended once a tool call has failed identically."""
    return (
        f"\n\nSTOP — you have called `{tool_name}` {attempts} times with identical "
        "arguments and it failed identically every time. Retrying the same call will "
        "fail again, and every attempt spends part of this run's request budget. "
        "Change approach before calling this tool again:\n"
        "- If you are editing a file, its current contents may differ from the copy "
        "you have in context (your own earlier edits in this run changed it, or it "
        "was updated elsewhere). Read the file, then copy the search text verbatim "
        "from what you just read.\n"
        "- If the text you are searching for is genuinely gone, the edit is already "
        "applied — move on.\n"
        "- If you cannot make it work, say so in your response instead of retrying."
    )


def log_tool_error(
    tool_name: str,
    error: Exception,
    *,
    conversation_id: str = "",
    level: str = "error",
) -> None:
    """Log a tool error with full stack trace and structured fields.

    Must be called inside an ``except`` block so the stack trace is captured.

    ``level`` is ``"error"`` for unexpected failures (trips the error-level
    Logfire alert) or ``"warn"`` for expected, model-recoverable tool-input
    errors (e.g. a sandbox ``EditError``) that should stay visible without
    paging.
    """
    fields = dict(
        tool_name=tool_name,
        error_type=type(error).__name__,
        error_message=str(error),
        conversation_id=conversation_id,
    )
    if level == "warn":
        logfire.warn("sernia tool error: {tool_name}", _exc_info=error, **fields)
    else:
        logfire.exception("sernia tool error: {tool_name}", **fields)


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

    Expected, model-recoverable errors (``_RECOVERABLE_EXCEPTIONS`` — sandbox
    file-tool errors like ``EditError``) are logged at warning level instead
    of error: the model fixes its arguments and retries, so they shouldn't
    page like a genuine failure. ``EditError`` additionally gets
    ``_EDIT_RECOVERY_HINT`` appended to the string the model sees, telling it
    to re-read the file instead of retrying a re-indented guess.

    Recoverable failures are also counted per run, keyed by tool name +
    arguments. Once the same call has failed ``REPEAT_ERROR_THRESHOLD`` times
    the error string gains a "STOP, change approach" instruction — a model
    re-sending identical arguments learns nothing from an identical error and
    will otherwise loop until the run's request limit is exhausted.

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
        except _RECOVERABLE_EXCEPTIONS as e:
            conversation_id = getattr(ctx.deps, "conversation_id", "")
            log_tool_error(name, e, conversation_id=conversation_id, level="warn")
            attempts = _count_identical_failure(ctx, name, tool_args)
            hint = _EDIT_RECOVERY_HINT if isinstance(e, EditError) else ""
            if attempts >= REPEAT_ERROR_THRESHOLD:
                return f"Error in {name}: {e}{hint}{_repeat_guidance(name, attempts)}"
            return f"Error in {name}: {e}{hint}"
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
