"""Authenticated team task listing, ClickUp creation, and status changes."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

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


class ClickUpList(BaseModel):
    id: str
    name: str
    state: Literal["ready", "unavailable", "incomplete"] = "ready"


class TasksView(BaseModel):
    clickup_lists: list[ClickUpList]
    tasks: list[Task]
    # Sources that could not be read just now; their tasks are missing from the list.
    unavailable: list[Source]
    # Sources with more tasks than a reader follows; some of their open tasks may be missing.
    incomplete: list[Source]


def soonest_first(tasks: list[Task]) -> list[Task]:
    """Dated tasks by date, then the undated ones; ties keep each source's own order."""
    latest = datetime.max.replace(tzinfo=UTC)
    return sorted(tasks, key=lambda task: (task.due is None, task.due or latest))


def verified_emails(user: SerniaUser) -> set[str]:
    return {
        address.email_address.strip().casefold()
        for address in user.email_addresses
        if address.verification and address.verification.status == "verified"
    }


@router.get("", response_model=TasksView)
async def list_open_tasks(user: SerniaUser):
    lists = [ClickUpList(id=key, name=name) for key, name in sources.CLICKUP_LISTS.items()]
    *clickup, quo = await asyncio.gather(
        *(sources.clickup_open(item.id) for item in lists),
        sources.quo_open(),
        return_exceptions=True,
    )
    tasks: list[Task] = []
    unavailable: list[Source] = []
    incomplete: list[Source] = []
    for item, result in zip(lists, clickup, strict=True):
        if isinstance(result, BaseException):
            logfire.error("team tasks: ClickUp list unavailable", list_id=item.id, _exc_info=result)
            item.state = "unavailable"
        else:
            tasks.extend(result.tasks)
            if not result.complete:
                item.state = "incomplete"
    if all(item.state == "unavailable" for item in lists):
        unavailable.append("clickup")
    elif any(item.state != "ready" for item in lists):
        incomplete.append("clickup")

    if isinstance(quo, BaseException):
        logfire.error("team tasks: Quo unavailable", _exc_info=quo)
        unavailable.append("quo")
    else:
        tasks.extend(quo.tasks)
        if not quo.complete:
            incomplete.append("quo")

    emails = verified_emails(user)
    for task in tasks:
        task.mine = not task.assignee_emails.isdisjoint(emails)
    return TasksView(
        tasks=soonest_first(tasks),
        unavailable=unavailable,
        incomplete=incomplete,
        clickup_lists=lists,
    )


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    list_id: str = Field(max_length=40)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)


@router.post("/clickup", response_model=Task, status_code=201)
async def create_task(body: CreateTask, user: SerniaUser):
    try:
        task = await sources.clickup_create(body.list_id, body.title, body.description)
    except sources.NotFound:
        raise HTTPException(422, "Choose a team ClickUp list.") from None
    except (httpx.HTTPError, sources.Unavailable):
        logfire.exception("team tasks: ClickUp creation failed", list_id=body.list_id)
        raise HTTPException(
            502, "Could not confirm creation. Check ClickUp before trying again."
        ) from None
    task.mine = not task.assignee_emails.isdisjoint(verified_emails(user))
    logfire.info("team tasks: ClickUp task created", task_id=task.id, user_id=user.id)
    return task


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
    task.mine = not task.assignee_emails.isdisjoint(verified_emails(user))
    logfire.info(
        "team tasks: status changed",
        source=source,
        task_id=task_id,
        completed=body.completed,
        user_id=user.id,
    )
    return task
