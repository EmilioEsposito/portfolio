# Sernia AI

Production AI assistant for Sernia Capital LLC's rental real estate business.

Built with **PydanticAI** (Graph Beta API), **FastAPI**, and integrated with OpenPhone, Gmail, Google Calendar/Drive, and ClickUp.

## Architecture

- **Agent** (`agent.py`) — Main PydanticAI agent with tool use, sub-agents, and persistent memory
- **Instructions** (`instructions.py`) — Static system prompt + dynamic context injection (datetime, memory, filetree, modality, triggers)
- **Config** (`config.py`) — Models, phone IDs, rate limits, and other tunables
- **Routes** (`routes.py`) — FastAPI endpoints for chat, conversations, approvals, and admin

## Documentation

| Document | Description |
|----------|-------------|
| [`tools/README.md`](tools/README.md) | SMS gates, mass-texting pattern, tool inventory |
| [`triggers/README.md`](triggers/README.md) | Trigger flows (team SMS, AI SMS, email) with diagrams |
| [`push/README.md`](push/README.md) | W3C Web Push + VAPID implementation |
| [`PLAN.md`](PLAN.md) | Master architecture document (design decisions, phases) |

## Key Concepts

### Modalities

The agent operates in three modalities, each with distinct behavior:

| Modality | Trigger | Tone | Response Channel |
|----------|---------|------|-----------------|
| `web_chat` | User message in web UI | Conversational, markdown | Web chat |
| `sms` | Inbound SMS to AI number | Short, direct, no markdown | SMS reply |
| `email` | Scheduled email processing | Professional, structured | Web chat (team alert) |

### Safety Gates

- **Internal/external SMS separation** — Internal phone numbers never appear in external threads
- **Unit isolation** — Cross-unit tenant group texts blocked (see [`tools/README.md`](tools/README.md))
- **HITL approval** — External SMS, emails, task/contact/calendar writes require human approval
- **Universal kill switch** — DB-backed toggle disables all automated triggers
- **Rate limiting** — Per-source cooldowns prevent runaway trigger loops

### Memory System

Git-backed persistent workspace at `/workspace/`:
- `MEMORY.md` — Long-term memory (injected into every conversation)
- `daily_notes/` — Date-stamped notes per topic
- `areas/` — Deep knowledge by domain (properties, tenants, etc.)
