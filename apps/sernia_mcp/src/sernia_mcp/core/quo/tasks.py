"""Quo v1 task requests, vendored from api/src/open_phone/tasks.py."""

from typing import Annotated, Literal, Self

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

TaskId = Annotated[str, Field(pattern=r"^TK[a-zA-Z0-9_-]+$")]
UserId = Annotated[str, Field(pattern=r"^US[a-zA-Z0-9_-]+$")]
ConversationId = Annotated[str, Field(pattern=r"^CN[a-zA-Z0-9_-]+$")]
PageSize = Annotated[int, Field(ge=1, le=100)]


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskText(TaskFields):
    """Quo replaces title and description together. Copy any unchanged text from the task."""

    title: str = Field(min_length=1)
    description: str


class NewTask(TaskText):
    """Link at most one inbox, conversation or activity. Omit all three for an org-wide task."""

    phone_number_id: Annotated[str, Field(pattern=r"^PN[a-zA-Z0-9_-]+$")] | None = None
    conversation_id: ConversationId | None = None
    activity_id: Annotated[str, Field(pattern=r"^AC[a-zA-Z0-9_-]+$")] | None = None
    assigned_to: UserId | None = None
    due_date: AwareDatetime | None = None

    @model_validator(mode="after")
    def single_link(self) -> Self:
        links = (self.phone_number_id, self.conversation_id, self.activity_id)
        if sum(link is not None for link in links) > 1:
            raise ValueError("Choose only one of phone_number_id, conversation_id or activity_id")
        return self


class TaskStatus(TaskFields):
    completed: bool


class TaskDueDate(TaskFields):
    """Pass null to remove the due date."""

    due_date: AwareDatetime | None


class TaskAssignment(TaskFields):
    action: Literal["assign", "unassign"]
    user_id: UserId


class TaskConversation(TaskFields):
    action: Literal["link", "unlink"]
    conversation_id: ConversationId


TaskChange = TaskText | TaskStatus | TaskDueDate | TaskAssignment | TaskConversation


async def list_tasks(client: httpx.AsyncClient, max_results: int, page_token: str | None) -> dict:
    params = {"maxResults": str(max_results)}
    if page_token:
        params["pageToken"] = page_token
    response = await client.get("/v1/tasks", params=params)
    response.raise_for_status()
    return response.json()


async def get_task(client: httpx.AsyncClient, task_id: TaskId) -> dict:
    response = await client.get(f"/v1/tasks/{task_id}")
    response.raise_for_status()
    return response.json()["data"]


async def create_task(client: httpx.AsyncClient, task: NewTask) -> dict:
    body = {
        to_camel(key): value
        for key, value in task.model_dump(mode="json", exclude_none=True).items()
    }
    response = await client.post("/v1/tasks", json=body)
    response.raise_for_status()
    return response.json()["data"]


async def update_task(client: httpx.AsyncClient, task_id: TaskId, change: TaskChange) -> dict:
    path = f"/v1/tasks/{task_id}"
    if isinstance(change, TaskText):
        response = await client.put(path, json=change.model_dump())
    elif isinstance(change, TaskStatus):
        action = "complete" if change.completed else "reopen"
        response = await client.post(f"{path}/{action}")
    elif isinstance(change, TaskDueDate):
        if change.due_date is None:
            response = await client.post(f"{path}/remove-due-date")
        else:
            response = await client.post(
                f"{path}/change-due-date", json={"dueDate": change.due_date.isoformat()}
            )
    elif isinstance(change, TaskAssignment):
        response = await client.post(f"{path}/{change.action}", json={"userId": change.user_id})
    else:
        response = await client.post(
            f"{path}/{change.action}-conversation", json={"conversationId": change.conversation_id}
        )
    response.raise_for_status()
    return response.json()["data"]


async def delete_task(client: httpx.AsyncClient, task_id: TaskId) -> str:
    response = await client.delete(f"/v1/tasks/{task_id}")
    response.raise_for_status()
    return f"Deleted Quo task {task_id}."
