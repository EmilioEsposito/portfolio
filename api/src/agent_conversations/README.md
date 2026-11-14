# Agent conversations

Storage and human-approval plumbing shared by every PydanticAI agent in this
API — the general Sernia agent, the Zillow Leads Agent, and the public demos.
Operational Web Push routes remain mounted under `/api/sernia-ai/push`.

| File | What it holds |
|------|---------------|
| `models.py` | The `agent_conversations` table (`AgentConversation`) and everything that reads or writes it: `save_agent_conversation`, `persist_agent_run_result`, `get_conversation_messages`, listing, deletion, and pending-approval lookup. |
| `hitl.py` | Agent-agnostic human-in-the-loop helpers: `extract_pending_approvals`, `resume_with_approvals`, `ApprovalDecision`. |

| `delivery.py` | Automatic-send reservations (`auto_approved_sends`), shared by both operational agents. |
| `identity.py` | Stable Sernia conversation and trigger identities. |
| [`push/`](push/README.md) | Web Push subscriptions and notifications for shared conversations. |

Things to know before changing it:

- **All three agents depend on storage and approval helpers.** A change here affects the public
  demos and Sernia's production runs. Keep it agent-agnostic — anything that
  knows about SMS, triggers or Sernia's delivery rules belongs in
  [`sernia_ai/runs.py`](../sernia_ai/runs.py).
- **Ownership differs by caller.** The demos filter conversations by
  `clerk_user_id`; Sernia passes `clerk_user_id=None` for shared team access.
- **Metadata merges on save.** `save_agent_conversation` merges `metadata`
  onto what is stored and leaves it untouched when `None`. See
  [Conversation metadata](../sernia_ai/README.md#conversation-metadata).
- **Saves use a fresh session.** `persist_agent_run_result` opens its own
  session and shields the write from cancellation, because the caller's
  session may have sat idle through a long model run.

Shared conversation HTTP routes still live in `sernia_ai/routes.py`; moving
those routes and handler dispatch is tracked in [#386](https://github.com/EmilioEsposito/portfolio/issues/386).
