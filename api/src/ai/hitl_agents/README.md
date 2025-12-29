# HITL (Human-in-the-Loop) Agents

Agents with tools that require user approval before execution.

## How It Works

1. **First run**: Agent proposes tool call → returns `DeferredToolRequests` → saved to DB
2. **User reviews**: Frontend shows approval UI with tool args (editable)
3. **Second run**: User approves/denies → `DeferredToolResults` → agent resumes

## Files

- `hitl_sms_agent.py` - SMS agent with `send_sms` tool (`requires_approval=True`)
- `routes.py` - API endpoints for chat, approval, and history

## Key Exports

```python
from api.src.ai.hitl_agents.hitl_sms_agent import (
    hitl_sms_agent,       # The agent (already has persistence patch applied)
    HITLAgentContext,     # Context dataclass with clerk_user_id, conversation_id
    resume_with_approvals, # Resume agent after user approves/denies
    extract_pending_approvals,  # Extract pending tool calls from result
    ApprovalDecision,     # Dataclass for approval/denial decision
)
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/ai/hitl-agent/chat` | Streaming chat (Vercel AI SDK) |
| `POST /api/ai/hitl-agent/conversation/{id}/approve` | Approve/deny pending tools |
| `GET /api/ai/hitl-agent/conversations/history` | List user's conversations |
| `GET /api/ai/hitl-agent/conversation/{id}/messages` | Load conversation messages |
| `DELETE /api/ai/hitl-agent/conversation/{id}` | Delete conversation |

## Usage Example

```python
# Start conversation
result = await hitl_sms_agent.run(
    user_prompt="Send hello to Emilio",
    deps=HITLAgentContext(clerk_user_id="user_123", conversation_id="conv_456"),
)

# Check for pending approval
pending = extract_pending_approvals(result)
if pending:
    # Show UI, get user decision, then resume
    final = await resume_with_approvals(
        conversation_id="conv_456",
        decisions=[ApprovalDecision(tool_call_id=p["tool_call_id"], approved=True)],
        clerk_user_id="user_123",
    )
```
