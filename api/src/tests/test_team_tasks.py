"""The team's Tasks page: ClickUp and Quo tasks read into one shape, and completed from it."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api.src.team_tasks import routes, sources
from api.src.team_tasks.sources import CLICKUP_MAINTENANCE_LIST_ID as LIST_ID
from api.src.team_tasks.sources import Task

DUE_MS = "1795478400000"  # 2026-11-24 00:00 UTC
DUE = datetime(2026, 11, 24, tzinfo=UTC)


def clickup_raw(task_id="86abc1", status="to do", kind="open", **more):
    return {
        "id": task_id,
        "name": "Replace the hallway light",
        "text_content": " The fixture flickers. ",
        "due_date": DUE_MS,
        "assignees": [{"username": "Avery Example", "email": "avery@example.com"}],
        "status": {"status": status, "type": kind},
        "url": f"https://app.clickup.com/t/{task_id}",
        "list": {"id": LIST_ID},
        **more,
    }


def quo_raw(task_id="TKone", **more):
    return {
        "taskId": task_id,
        "title": "Call the plumber back",
        "description": "About the estimate.",
        "dueDate": "2026-11-25T15:00:00.000Z",
        "assignedTo": "USone",
        "completed": False,
        "isDeleted": False,
        "conversationId": "CNone",
        **more,
    }


STATUSES = [
    {"status": "to do", "type": "open"},
    {"status": "in progress", "type": "custom"},
    {"status": "waiting", "type": "custom"},
    {"status": "complete", "type": "closed"},
]


def fake(handler, base_url):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def test_a_clickup_task_is_read_into_the_shared_shape():
    task = sources.clickup_task(clickup_raw(status="waiting", kind="custom"))
    assert task == Task(
        source="clickup",
        id="86abc1",
        title="Replace the hallway light",
        description="The fixture flickers.",
        due=DUE,
        assignees=["Avery Example"],
        status="waiting",
        completed=False,
        url="https://app.clickup.com/t/86abc1",
    )
    bare = sources.clickup_task({"id": "86abc2", "name": None, "status": {"type": "closed"}})
    assert (bare.title, bare.description, bare.due, bare.assignees) == (
        "Untitled task",
        "",
        None,
        [],
    )
    assert bare.completed is True


def test_a_quo_task_is_read_into_the_shared_shape():
    task = sources.quo_task(quo_raw(), {"USone": "Blake Example"})
    assert task == Task(
        source="quo",
        id="TKone",
        title="Call the plumber back",
        description="About the estimate.",
        due=datetime(2026, 11, 25, 15, tzinfo=UTC),
        assignees=["Blake Example"],
        status="open",
        completed=False,
        # Quo has no web address for a task.
        url=None,
    )
    unassigned = sources.quo_task(quo_raw(assignedTo=None, dueDate=None, completed=True), {})
    assert (unassigned.assignees, unassigned.due, unassigned.status) == ([], None, "complete")
    assert sources.quo_task(quo_raw(assignedTo="USgone"), {}).assignees == ["Someone"]


@pytest.mark.asyncio
async def test_clickup_reads_every_page_of_the_maintenance_list_and_only_open_tasks():
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v2/list/{LIST_ID}/task"
        asked.append(dict(request.url.params))
        page = int(request.url.params["page"])
        return httpx.Response(
            200, json={"tasks": [clickup_raw(f"86p{page}")], "last_page": page == 1}
        )

    tasks = await sources.clickup_open(fake(handler, "https://api.clickup.com/api/v2"))
    assert [task.id for task in tasks] == ["86p0", "86p1"]
    assert asked[0] == {"include_closed": "false", "subtasks": "true", "page": "0"}


@pytest.mark.asyncio
async def test_quo_leaves_out_completed_and_deleted_tasks_and_names_the_assignee():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/users":
            return httpx.Response(
                200, json={"data": [{"id": "USone", "firstName": "Blake", "lastName": "Example"}]}
            )
        assert request.url.path == "/v1/tasks"
        if request.url.params.get("pageToken") == "next":
            return httpx.Response(200, json={"data": [quo_raw("TKthree")]})
        return httpx.Response(
            200,
            json={
                "data": [
                    quo_raw("TKone"),
                    quo_raw("TKdone", completed=True),
                    quo_raw("TKgone", isDeleted=True),
                ],
                "nextPageToken": "next",
            },
        )

    tasks = await sources.quo_open(fake(handler, "https://api.openphone.com"))
    assert [task.id for task in tasks] == ["TKone", "TKthree"]
    assert tasks[0].assignees == ["Blake Example"]


def clickup_writes(task_list=LIST_ID, lookup=200):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            body = json.loads(request.content)
            sent.append(body)
            kind = next(item["type"] for item in STATUSES if item["status"] == body["status"])
            return httpx.Response(200, json=clickup_raw(status=body["status"], kind=kind))
        if request.url.path.endswith(f"/list/{LIST_ID}"):
            return httpx.Response(200, json={"statuses": STATUSES})
        return httpx.Response(lookup, json=clickup_raw(list={"id": task_list}))

    return fake(handler, "https://api.clickup.com/api/v2"), sent


@pytest.mark.asyncio
async def test_completing_a_clickup_task_uses_the_lists_own_closed_status():
    client, sent = clickup_writes()
    task = await sources.clickup_set_completed("86abc1", True, client=client)
    assert sent == [{"status": "complete"}]
    assert task.completed is True


@pytest.mark.asyncio
async def test_undoing_puts_a_clickup_task_back_where_it_was():
    client, sent = clickup_writes()
    task = await sources.clickup_set_completed("86abc1", False, "waiting", client=client)
    assert sent == [{"status": "waiting"}]
    assert (task.status, task.completed) == ("waiting", False)

    # A status the list does not have, or its closed one, falls back to the open status.
    for restore in (None, "archived", "complete"):
        client, sent = clickup_writes()
        await sources.clickup_set_completed("86abc1", False, restore, client=client)
        assert sent == [{"status": "to do"}], restore


@pytest.mark.asyncio
async def test_only_tasks_of_the_maintenance_list_can_be_changed():
    for client, _ in (
        clickup_writes(task_list="901300000000"),
        clickup_writes(lookup=404),
        clickup_writes(lookup=401),
    ):
        with pytest.raises(sources.NotFound):
            await sources.clickup_set_completed("86abc1", True, client=client)
    # Nothing that is not a ClickUp id reaches ClickUp at all.
    for bad in ("../team", "86abc1/comment", ""):
        with pytest.raises(sources.NotFound):
            await sources.clickup_set_completed(bad, True, client=clickup_writes()[0])


@pytest.mark.asyncio
async def test_completing_and_reopening_a_quo_task():
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/users":
            return httpx.Response(200, json={"data": []})
        called.append(request.url.path)
        if "TKmissing" in request.url.path:
            return httpx.Response(404, json={})
        return httpx.Response(
            200, json={"data": quo_raw(completed=request.url.path.endswith("/complete"))}
        )

    done = await sources.quo_set_completed(
        "TKone", True, client=fake(handler, "https://api.openphone.com")
    )
    again = await sources.quo_set_completed(
        "TKone", False, "open", client=fake(handler, "https://api.openphone.com")
    )
    assert called == ["/v1/tasks/TKone/complete", "/v1/tasks/TKone/reopen"]
    assert (done.completed, again.completed) == (True, False)
    with pytest.raises(sources.NotFound):
        await sources.quo_set_completed(
            "TKmissing", True, client=fake(handler, "https://api.openphone.com")
        )
    with pytest.raises(sources.NotFound):
        await sources.quo_set_completed("86abc1", True)


@pytest.mark.asyncio
async def test_a_source_without_its_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_KEY", raising=False)
    monkeypatch.delenv("QUO_API_KEY_PORTFOLIO", raising=False)
    for read in (sources.clickup_open, sources.quo_open):
        with pytest.raises(sources.Unavailable):
            await read()


def task(source, task_id, due):
    return Task(
        source=source,
        id=task_id,
        title=task_id,
        description="",
        due=due,
        assignees=[],
        status="open",
        completed=False,
        url=None,
    )


def app(allowed=True):
    application = FastAPI()
    application.include_router(routes.router)

    async def caller():
        if not allowed:
            raise HTTPException(403, "A verified Sernia account is required.")
        return SimpleNamespace(id="test-user")

    application.dependency_overrides[routes.verify_serniacapital_user] = caller
    return application


def browser(allowed=True):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(allowed)), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_the_list_is_both_sources_soonest_first_with_undated_tasks_last(monkeypatch):
    async def clickup():
        return [
            task("clickup", "later", datetime(2026, 12, 1, tzinfo=UTC)),
            task("clickup", "undated", None),
        ]

    async def quo():
        return [task("quo", "sooner", datetime(2026, 11, 24, tzinfo=UTC))]

    monkeypatch.setattr(sources, "READERS", {"clickup": clickup, "quo": quo})
    async with browser() as client:
        body = (await client.get("/team-tasks")).json()
    assert [item["id"] for item in body["tasks"]] == ["sooner", "later", "undated"]
    assert body["unavailable"] == []


@pytest.mark.asyncio
async def test_one_source_failing_still_shows_the_other(monkeypatch):
    async def clickup():
        raise httpx.ConnectError("no route")

    async def quo():
        return [task("quo", "TKone", None)]

    monkeypatch.setattr(sources, "READERS", {"clickup": clickup, "quo": quo})
    async with browser() as client:
        response = await client.get("/team-tasks")
    assert response.status_code == 200
    assert response.json()["unavailable"] == ["clickup"]
    assert [item["id"] for item in response.json()["tasks"]] == ["TKone"]


@pytest.mark.asyncio
async def test_changing_a_status_answers_the_task_or_says_why_not(monkeypatch):
    seen: list[tuple] = []

    async def clickup(task_id, completed, restore):
        seen.append((task_id, completed, restore))
        if task_id == "gone":
            raise sources.NotFound(task_id)
        if task_id == "down":
            raise httpx.ConnectError("no route")
        return task("clickup", task_id, None).model_copy(update={"completed": completed})

    monkeypatch.setattr(sources, "WRITERS", {"clickup": clickup, "quo": clickup})
    async with browser() as client:
        done = await client.post("/team-tasks/clickup/86abc1/status", json={"completed": True})
        undone = await client.post(
            "/team-tasks/clickup/86abc1/status", json={"completed": False, "restore": "waiting"}
        )
        gone = await client.post("/team-tasks/clickup/gone/status", json={"completed": True})
        down = await client.post("/team-tasks/quo/down/status", json={"completed": True})
        unknown = await client.post("/team-tasks/trello/1/status", json={"completed": True})
        loose = await client.post("/team-tasks/quo/TKone/status", json={"completed": "yes"})
    assert (done.status_code, done.json()["completed"]) == (200, True)
    assert seen[:2] == [("86abc1", True, None), ("86abc1", False, "waiting")]
    assert undone.json()["completed"] is False
    assert gone.status_code == 404
    assert (down.status_code, down.json()["detail"]) == (
        502,
        "Quo did not take the change. Try again.",
    )
    assert unknown.status_code == 422
    assert loose.status_code == 422


@pytest.mark.asyncio
async def test_tasks_require_a_verified_sernia_user():
    async with browser(allowed=False) as client:
        assert (await client.get("/team-tasks")).status_code == 403
        change = await client.post("/team-tasks/quo/TKone/status", json={"completed": True})
        assert change.status_code == 403
    assert [dep.dependency for dep in routes.router.dependencies] == [
        routes.verify_serniacapital_user
    ]
