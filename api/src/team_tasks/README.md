# Team tasks

The signed-in team page at `/team/tasks` combines the Sernia Other Todo, Leasing
and MaintenanceRequest lists in ClickUp (including subtasks) with open Quo
tasks. The API reads each list independently and sorts by due date, undated last.

My Tasks matches the signed-in person's verified Clerk email addresses against
ClickUp assignee emails and the assigned Quo user's email. A verified staff
member using a separate ClickUp login can also match by the stable member ID
in `CLICKUP_TEAM_EMAIL_BY_MEMBER_ID`. The navigation and
team home count that person's open tasks. All tasks includes teammates and
unassigned work. Both views can be filtered by list. Completed rows stay on
screen for Undo and drop out of open counts immediately.

All endpoints require a verified Sernia Capital user.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/team-tasks` | Returns open tasks, a personal assignment flag on each row, the three ClickUp lists with their read status, and source availability. Partial reads keep the other lists visible. |
| `POST /api/team-tasks/clickup` | Creates a task using `list_id`, `title` and optional `description`. Omits assignees, due date and status so ClickUp can apply list defaults. |
| `POST /api/team-tasks/{source}/{task_id}/status` | Completes or reopens the task. ClickUp writes check the task's current list against the same three-list allowlist and use its own statuses. Undo restores the previous status if available. |

Quo's reader pages through completed and deleted tasks to find the open ones,
up to `QUO_MAX_PAGES`. Each ClickUp list has its own `CLICKUP_MAX_PAGES` limit.
