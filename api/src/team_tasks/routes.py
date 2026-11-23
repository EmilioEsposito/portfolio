"""The team's Tasks page: open ClickUp and Quo tasks in one list.

- ``GET /team-tasks``: the open tasks of both sources, soonest due first. A
  source that cannot be read is named in ``unavailable`` and the other is
  still shown.
- ``POST /team-tasks/{source}/{task_id}/status``: complete a task, or reopen
  one just completed (the page's Undo).

Everything else about a task (creating, editing, assigning, its date) is done
in the source's own app or by Sernia AI.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import logfire
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.src.team_tasks import sources
from api.src.team_tasks.sources import Source, Task
from api.src.utils.clerk import SerniaUser, verify_serniacapital_user

router = APIRouter(
    prefix="/team-tasks",
    tags=["team-tasks"],
    dependencies=[Depends(verify_serniacapital_user)],
)

NAMES: dict[Source, str] = {"clickup": "ClickUp", "quo": "Quo"}


class TasksView(BaseModel):
    tasks: list[Task]
    # Sources that could not be read just now; their tasks are missing from the list.
    unavailable: list[Source]


def soonest_first(tasks: list[Task]) -> list[Task]:
    """Dated tasks by date, then the undated ones; ties keep each source's own order."""
    latest = datetime.max.replace(tzinfo=UTC)
    return sorted(tasks, key=lambda task: (task.due is None, task.due or latest))


@router.get("", response_model=TasksView)
async def list_open_tasks():
    order = list(sources.READERS)
    results = await asyncio.gather(
        *(sources.READERS[source]() for source in order), return_exceptions=True
    )
    tasks: list[Task] = []
    unavailable: list[Source] = []
    for source, result in zip(order, results, strict=True):
        if isinstance(result, BaseException):
            logfire.error("team tasks: source could not be read", source=source, _exc_info=result)
            unavailable.append(source)
        else:
            tasks.extend(result)
    return TasksView(tasks=soonest_first(tasks), unavailable=unavailable)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: StrictBool
    # When reopening: the status the task had before it was completed, so that
    # an undo puts it back where it was. Ignored if the source has no such status.
    restore: str | None = Field(default=None, max_length=80)


@router.post("/{source}/{task_id}/status", response_model=Task)
async def set_status(source: Source, task_id: str, body: StatusChange, user: SerniaUser):
    try:
        task = await sources.WRITERS[source](task_id, body.completed, body.restore)
    except sources.NotFound:
        raise HTTPException(404, "That task is no longer there. Reload the list.") from None
    except (httpx.HTTPError, sources.Unavailable):
        logfire.exception("team tasks: status change failed", source=source, task_id=task_id)
        raise HTTPException(502, f"{NAMES[source]} did not take the change. Try again.") from None
    logfire.info(
        "team tasks: status changed",
        source=source,
        task_id=task_id,
        completed=body.completed,
        user_id=user.id,
    )
    return task
