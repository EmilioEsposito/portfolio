# Agent conversations

Storage and human-approval plumbing shared by every PydanticAI agent in this
API — the operational Sernia agent and the public demos alike. It has no
routes of its own.

| File | What it holds |
|------|---------------|
| `models.py` | The `agent_conversations` table (`AgentConversation`) and everything that reads or writes it: `save_agent_conversation`, `persist_agent_run_result`, `get_conversation_messages`, listing, deletion, and pending-approval lookup. |
| `hitl.py` | Agent-agnostic human-in-the-loop helpers: `extract_pending_approvals`, `resume_with_approvals`, `ApprovalDecision`. |

Things to know before changing it:

- **Both kinds of agent depend on it.** A change here affects the public
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
