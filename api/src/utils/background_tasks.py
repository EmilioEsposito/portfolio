"""Background task creation with immediate error reporting."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import logfire


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
