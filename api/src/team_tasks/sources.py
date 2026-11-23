"""Open tasks from the two places the team keeps them, in one small shape.

Nothing is stored and nothing joins the two. Each source has a reader and a way
to complete or reopen a task; the routes only put the two lists together.
Dropping a source means deleting its section here and its name from ``Source``.

- ClickUp: the maintenance list (``CLICKUP_MAINTENANCE_LIST_ID``), subtasks
  included. Its due date is what the list calls the reminder date.
- Quo: every task in the workspace. Quo has no web address for a task, so a
  Quo task carries no link.
"""

import os
import re
from datetime import UTC, datetime
from typing import Literal, NamedTuple

import httpx
from pydantic import AwareDatetime, BaseModel

from api.src.open_phone import tasks as quo_tasks
from api.src.sernia_ai.config import CLICKUP_MAINTENANCE_LIST_ID

Source = Literal["clickup", "quo"]

# How many pages a reader follows before it stops and says its list is cut
# short. ClickUp's pages hold open tasks only (100 a page). Quo's hold every
# task ever made, completed and deleted ones too, and Quo has no filter for
# them, so its reader follows far more.
CLICKUP_MAX_PAGES = 10
QUO_MAX_PAGES = 50
QUO_PAGE_SIZE = 100


class Task(BaseModel):
    source: Source
    id: str
    title: str
    description: str
    due: AwareDatetime | None
    assignees: list[str]
    # The source's own word for where the task stands ("to do", "waiting", "open").
    status: str
    completed: bool
    # The task in its own app, where that app has an address for it.
    url: str | None


class Listing(NamedTuple):
    tasks: list[Task]
    # False when the source had more pages than the reader follows, so open
    # tasks may be missing. The page says so instead of showing a short list
    # as if it were whole.
    complete: bool = True


class Unavailable(Exception):
    """The source is not set up here."""


class NotFound(Exception):
    """No such task among the ones this page shows."""


# ---------------------------------------------------------------------------
# ClickUp
# ---------------------------------------------------------------------------

_CLICKUP_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _clickup_client() -> httpx.AsyncClient:
    key = os.environ.get("CLICKUP_API_KEY", "")
    if not key:
        raise Unavailable("CLICKUP_API_KEY is not set")
    return httpx.AsyncClient(
        base_url="https://api.clickup.com/api/v2",
        headers={"accept": "application/json", "Authorization": key},
        timeout=20,
    )


def clickup_task(raw: dict) -> Task:
    status = raw.get("status") or {}
    due = raw.get("due_date")
    return Task(
        source="clickup",
        id=str(raw["id"]),
        title=raw.get("name") or "Untitled task",
        description=(raw.get("text_content") or raw.get("description") or "").strip(),
        # ClickUp gives dates as milliseconds since 1970, in a string.
        due=datetime.fromtimestamp(int(due) / 1000, UTC) if due else None,
        assignees=[
            person.get("username") or person.get("email") or "Someone"
            for person in raw.get("assignees") or []
        ],
        status=status.get("status") or "",
        completed=status.get("type") == "closed",
        url=raw.get("url"),
    )


async def clickup_open(client: httpx.AsyncClient | None = None) -> Listing:
    """Every task in the maintenance list that is not closed."""
    async with client or _clickup_client() as http:
        found: list[Task] = []
        for page in range(CLICKUP_MAX_PAGES):
            response = await http.get(
                f"/list/{CLICKUP_MAINTENANCE_LIST_ID}/task",
                params={"include_closed": "false", "subtasks": "true", "page": str(page)},
            )
            response.raise_for_status()
            body = response.json()
            found.extend(clickup_task(raw) for raw in body.get("tasks") or [])
            if body.get("last_page", True):
                return Listing(found)
        return Listing(found, complete=False)


async def clickup_set_completed(
    task_id: str,
    completed: bool,
    restore: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Task:
    """Moves the task to its list's closed status, or back out of it.

    Reopening returns it to ``restore`` (the status it had before, as the page
    remembers it) when the list still has that status, else to the list's
    open status.
    """
    if not _CLICKUP_TASK_ID.fullmatch(task_id):
        raise NotFound(task_id)
    async with client or _clickup_client() as http:
        current = await http.get(f"/task/{task_id}")
        if current.status_code in (401, 404):
            # ClickUp answers 401 for a task the key cannot see.
            raise NotFound(task_id)
        current.raise_for_status()
        # Only the list this page shows: the key itself can reach every list.
        if (current.json().get("list") or {}).get("id") != CLICKUP_MAINTENANCE_LIST_ID:
            raise NotFound(task_id)
        listing = await http.get(f"/list/{CLICKUP_MAINTENANCE_LIST_ID}")
        listing.raise_for_status()
        statuses = listing.json().get("statuses") or []
        wanted = "closed" if completed else "open"
        name = next((item["status"] for item in statuses if item.get("type") == wanted), None)
        if not completed and any(
            item["status"] == restore and item.get("type") != "closed" for item in statuses
        ):
            name = restore
        if name is None:
            raise Unavailable(f"The ClickUp list has no {wanted} status")
        changed = await http.put(f"/task/{task_id}", json={"status": name})
        changed.raise_for_status()
        return clickup_task(changed.json())


# ---------------------------------------------------------------------------
# Quo
# ---------------------------------------------------------------------------


def _quo_client() -> httpx.AsyncClient:
    from api.src.open_phone.rate_limit import build_rate_limited_transport

    key = os.environ.get("QUO_API_KEY_PORTFOLIO", "")
    if not key:
        raise Unavailable("QUO_API_KEY_PORTFOLIO is not set")
    return httpx.AsyncClient(
        base_url="https://api.openphone.com",
        headers={"Authorization": key},
        timeout=20,
        transport=build_rate_limited_transport(),
    )


def quo_task(raw: dict, names: dict[str, str]) -> Task:
    """``names`` maps a Quo user id to the person's name."""
    assigned = raw.get("assignedTo")
    due = raw.get("dueDate")
    completed = bool(raw.get("completed"))
    return Task(
        source="quo",
        id=raw["taskId"],
        title=raw.get("title") or "Untitled task",
        description=(raw.get("description") or "").strip(),
        due=datetime.fromisoformat(due.replace("Z", "+00:00")) if due else None,
        assignees=[names.get(assigned, "Someone")] if assigned else [],
        status="complete" if completed else "open",
        completed=completed,
        url=None,
    )


async def _quo_names(http: httpx.AsyncClient) -> dict[str, str]:
    response = await http.get("/v1/users", params={"maxResults": "50"})
    response.raise_for_status()
    return {
        user["id"]: " ".join(part for part in (user.get("firstName"), user.get("lastName")) if part)
        or user.get("email")
        or "Someone"
        for user in response.json().get("data") or []
    }


async def quo_open(client: httpx.AsyncClient | None = None) -> Listing:
    """Every Quo task that is neither completed nor deleted."""
    async with client or _quo_client() as http:
        names = await _quo_names(http)
        found: list[Task] = []
        token: str | None = None
        for _ in range(QUO_MAX_PAGES):
            body = await quo_tasks.list_tasks(http, QUO_PAGE_SIZE, token)
            found.extend(
                quo_task(raw, names)
                for raw in body.get("data") or []
                if not raw.get("completed") and not raw.get("isDeleted")
            )
            token = body.get("nextPageToken")
            if not token:
                return Listing(found)
        return Listing(found, complete=False)


async def quo_set_completed(
    task_id: str,
    completed: bool,
    restore: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Task:
    """Completes or reopens the task. Quo has one open state, so ``restore`` has no say."""
    if not re.fullmatch(r"TK[a-zA-Z0-9_-]+", task_id):
        raise NotFound(task_id)
    async with client or _quo_client() as http:
        try:
            raw = await quo_tasks.update_task(
                http, task_id, quo_tasks.TaskStatus(completed=completed)
            )
        except httpx.HTTPStatusError as failure:
            if failure.response.status_code == 404:
                raise NotFound(task_id) from None
            raise
        try:
            names = await _quo_names(http)
        except (httpx.HTTPError, ValueError, KeyError):
            # The task has changed already. A failed lookup of people's names
            # must not be reported as the change failing.
            names = {}
        return quo_task(raw, names)


READERS = {"clickup": clickup_open, "quo": quo_open}
WRITERS = {"clickup": clickup_set_completed, "quo": quo_set_completed}
