# Agent conversations

Conversation storage and approval helpers used by the general Sernia agent,
the Zillow Leads Agent, and public demos. The HTTP router serves the verified
Sernia team; public demos keep their own routes and user-scoped access.

| File | Responsibility |
| --- | --- |
| `models.py` | Conversation storage, pending approvals, history, and persistence |
| `routes.py` | Team conversation details, messages, history, pending queue, approval dispatch, and deletion |
| `auth.py` | Verified-Sernia router gate and current-user identity helpers |
| `schemas.py` | Existing approval request payloads |
| `hitl.py` | PydanticAI approval extraction, argument overrides, and continuation |
| `history.py` | Existing Quo enrichment for SMS history; imports the SMS adapter only when needed |
| `run_state.py` | Stored run status, expiry interpretation, admission locks, and active-run checks |
| `errors.py` | Existing provider-error HTTP status and message mapping |
| `delivery.py` | Automatic-send reservations in `auto_approved_sends` |
| `identity.py` | Stable Sernia conversation and trigger identities |
| [`push/`](push/README.md) | Web Push subscriptions and notifications |

## HTTP compatibility

`api/index.py` mounts the same authenticated router at `/api/agent-conversations`
and `/api/sernia-ai`. Existing web and mobile clients keep their current URLs;
the new namespace is an additive alias. Request bodies, responses, shared-team
access, and error status codes are unchanged.

| Method | Path under either prefix |
| --- | --- |
| GET | `/conversation/{conversation_id}` |
| GET | `/conversation/{conversation_id}/messages` |
| GET | `/conversations/history` |
| GET | `/workflow/pending` |
| POST | `/conversation/{conversation_id}/approve` |
| DELETE | `/conversation/{conversation_id}` |

General chat, cancellation, context inspection, and general-agent settings remain
in `sernia_ai/routes.py` under `/api/sernia-ai`. Web Push keeps its existing
`/api/sernia-ai/push` mount. No table, stored identifier, migration, or client
cutover is required.

## Approval ownership

The router reads the saved conversation before choosing a handler. A request
cannot select an agent. Conversations marked `zillow_lead_agent` call
[`zillow_leads/handle.py`](../zillow_leads/handle.py); all existing general-agent
conversations retain the general continuation path in
[`sernia_ai/conversation_handler.py`](../sernia_ai/conversation_handler.py).

Zillow approval sends only the approved text to the stored route, or records a
rejection/acknowledgment without sending. Its row lock still serializes decisions
across both URL mounts. It never resumes a model with general tools.

The general handler owns deps, model selection, captured run messages, approval
continuation, finalization, SMS reply ownership, and partial-history persistence
on failure. Active-run checks and existing lock boundaries are unchanged. No
approval or interrupted run is automatically replayed by this refactor.

Importing the shared router does not construct either agent. Ordinary history
reads use stored messages; the existing SMS path loads its Quo adapter on demand.
General execution code is imported only after selecting the general handler.

## Storage details

- Demo callers filter by `clerk_user_id`; operational callers retain shared team
  access with `clerk_user_id=None`.
- Saves merge metadata and leave it untouched when no new metadata is supplied.
  See [conversation metadata](../sernia_ai/README.md#conversation-metadata).
- `persist_agent_run_result` uses a fresh session and shields persistence from
  cancellation after a long model run.

`test_shared_conversation_routes.py` covers both mounts, domain authentication,
shared history, approval/rejection, stored-handler and recipient selection,
concurrent/stale lead decisions, error responses, and import isolation. Existing
chat lifecycle tests cover cancellation, active-run rejection, and saved progress.
