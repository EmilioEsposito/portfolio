"""Read and update the team's three ClickUp lists and workspace Quo tasks."""

import os
import re
from datetime import UTC, datetime
from typing import Literal, NamedTuple

import httpx
from pydantic import AwareDatetime, BaseModel, Field

from api.src.open_phone import tasks as quo_tasks
from api.src.sernia_ai.config import CLICKUP_MAINTENANCE_LIST_ID

CLICKUP_LISTS = {
    "901311922390": "Sernia Other Todo",
    "901311922677": "Leasing",
    CLICKUP_MAINTENANCE_LIST_ID: "Maintenance",
}

# Staff who use a different email for ClickUp and their team login. Key by
# the stable workspace member ID so a display-name change cannot move tasks.
CLICKUP_TEAM_EMAIL_BY_MEMBER_ID = {"94066437": "peppino@serniacapital.com"}

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
    list_id: str | None = None
    mine: bool = False
    assignee_emails: set[str] = Field(default_factory=set, exclude=True, repr=False)
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


def clickup_task(raw: dict, list_id: str | None = None) -> Task:
    status = raw.get("status") or {}
    due = raw.get("due_date")
    assignees = raw.get("assignees") or []
    emails = {person["email"].strip().casefold() for person in assignees if person.get("email")}
    for person in assignees:
        team_email = CLICKUP_TEAM_EMAIL_BY_MEMBER_ID.get(str(person.get("id")))
        if team_email:
            emails.add(team_email)
    return Task(
        source="clickup",
        list_id=list_id or (raw.get("list") or {}).get("id"),
        assignee_emails=emails,
        id=str(raw["id"]),
        title=raw.get("name") or "Untitled task",
        description=(raw.get("text_content") or raw.get("description") or "").strip(),
        # ClickUp gives dates as milliseconds since 1970, in a string.
        due=datetime.fromtimestamp(int(due) / 1000, UTC) if due else None,
        assignees=[
            person.get("username") or person.get("email") or "Someone" for person in assignees
        ],
        status=status.get("status") or "",
        completed=status.get("type") == "closed",
        url=raw.get("url"),
    )


async def clickup_open(list_id: str, client: httpx.AsyncClient | None = None) -> Listing:
    """Every task in the requested team list that is not closed."""
    async with client or _clickup_client() as http:
        found: list[Task] = []
        for page in range(CLICKUP_MAX_PAGES):
            response = await http.get(
                f"/list/{list_id}/task",
                params={"include_closed": "false", "subtasks": "true", "page": str(page)},
            )
            response.raise_for_status()
            body = response.json()
            found.extend(clickup_task(raw, list_id) for raw in body.get("tasks") or [])
            if body.get("last_page", True):
                return Listing(found)
        return Listing(found, complete=False)


async def clickup_create(list_id: str, title: str, description: str) -> Task:
    if list_id not in CLICKUP_LISTS:
        raise NotFound(list_id)
    body = {"name": title}
    if description:
        body["description"] = description
    async with _clickup_client() as http:
        response = await http.post(f"/list/{list_id}/task", json=body)
        response.raise_for_status()
        return clickup_task(response.json(), list_id)


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
        # The integration key can also reach lists outside the team page.
        list_id = (current.json().get("list") or {}).get("id")
        if list_id not in CLICKUP_LISTS:
            raise NotFound(task_id)
        listing = await http.get(f"/list/{list_id}")
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


def quo_task(raw: dict, users: dict[str, dict]) -> Task:
    assigned = raw.get("assignedTo")
    person = users.get(assigned, {})
    email = person.get("email") or ""
    name = " ".join(part for part in (person.get("firstName"), person.get("lastName")) if part)
    due = raw.get("dueDate")
    completed = bool(raw.get("completed"))
    return Task(
        source="quo",
        id=raw["taskId"],
        title=raw.get("title") or "Untitled task",
        description=(raw.get("description") or "").strip(),
        due=datetime.fromisoformat(due.replace("Z", "+00:00")) if due else None,
        assignees=[name or email or "Someone"] if assigned else [],
        assignee_emails={email.strip().casefold()} if email else set(),
        status="complete" if completed else "open",
        completed=completed,
        url=None,
    )


async def _quo_users(http: httpx.AsyncClient) -> dict[str, dict]:
    users = {}
    params = {"maxResults": "50"}
    while True:
        response = await http.get("/v1/users", params=params)
        response.raise_for_status()
        page = response.json()
        users.update({user["id"]: user for user in page.get("data") or []})
        token = page.get("nextPageToken")
        if not token:
            return users
        params["pageToken"] = token


async def quo_open(client: httpx.AsyncClient | None = None) -> Listing:
    """Every Quo task that is neither completed nor deleted."""
    async with client or _quo_client() as http:
        users = await _quo_users(http)
        found: list[Task] = []
        token: str | None = None
        for _ in range(QUO_MAX_PAGES):
            body = await quo_tasks.list_tasks(http, QUO_PAGE_SIZE, token)
            found.extend(
                quo_task(raw, users)
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
            users = await _quo_users(http)
        except (httpx.HTTPError, ValueError, KeyError):
            # The task has changed already. A failed lookup of people's names
            # must not be reported as the change failing.
            users = {}
        return quo_task(raw, users)


WRITERS = {"clickup": clickup_set_completed, "quo": quo_set_completed}
