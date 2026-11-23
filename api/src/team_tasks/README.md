# Team tasks

The team's Tasks page (`/team/tasks`) lists the open tasks from the two places
the team keeps them and lets a person mark one complete.

| Source | Which tasks | Link |
| --- | --- | --- |
| ClickUp | The maintenance list (`CLICKUP_MAINTENANCE_LIST_ID`), subtasks included, every status that is not closed | The task in ClickUp |
| Quo | Every task in the workspace that is not completed or deleted | None: Quo has no web address for a task |

Nothing is stored and there is no shared layer. `sources.py` has one reader and
one writer per source, each mapping into the same `Task` shape (title,
description, due date, assignees, status, link). Dropping a source means
deleting its section there, its name from `Source`, and its entries in
`READERS` and `WRITERS`.

## Endpoints

Both need a verified `@serniacapital.com` user.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/team-tasks` | Open tasks of both sources, soonest due first, undated last. A source that cannot be read is named in `unavailable`; the other is still returned. A source with more pages than its reader follows is named in `incomplete`, and the page says some tasks may be missing |
| `POST /api/team-tasks/{source}/{task_id}/status` | `{"completed": true}` completes a task. `{"completed": false, "restore": "waiting"}` reopens it, in ClickUp to the status it had before when the list still has it |

Quo lists every task ever made, completed and deleted ones included, and has
no filter for them, so the Quo reader pages through all of them (up to
`QUO_MAX_PAGES` pages of 100) and keeps the open ones. The page gets slower as
that history grows.

Completing a ClickUp task moves it to the list's own closed status, read from
the list each time. Only tasks of the maintenance list can be changed, though
the key can reach every list.

Creating, editing, assigning and dating tasks is left to each app and to
Sernia AI's ClickUp and Quo tools.
