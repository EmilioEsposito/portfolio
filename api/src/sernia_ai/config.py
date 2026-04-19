"""
Configuration for the Sernia AI agent.

Keep tunables here so they're easy to find and tweak.
"""
import os
from pathlib import Path

# Web search: only these domains are allowed.
# Used by the builtin WebSearchTool (supported by Anthropic, OpenAI Responses,
# Groq, Google, xAI, OpenRouter — see pydantic_ai.builtin_tools.WebSearchTool).
WEB_SEARCH_ALLOWED_DOMAINS: list[str] = [
    "zillow.com",
    "redfin.com",
    # "realtor.com",  # Blocks Anthropic's crawler
    "apartments.com",
    "rentometer.com",
    "clickup.com",
    "serniacapital.com",
    # Add more as needed,
    "platform.claude.com",
    "support.claude.com",
    "code.claude.com",
    "developers.openai.com",
    "docs.openai.com",
    "support.openai.com",
    "platform.openai.com",
]

# Compaction: trigger at ~85% of context window token estimate.
# gpt-5.4 has a 200k context window (same as Claude Sonnet 4.6); adjust if you
# swap to a smaller model.
TOKEN_COMPACTION_THRESHOLD = 170_000

# Summarization: tool results larger than this (chars) get summarized by the sub-agent.
SUMMARIZATION_CHAR_THRESHOLD = 10_000

# Main agent model. Use `openai-responses:` (not `openai:`) so builtin tools
# like WebSearchTool work — the Chat Completions API doesn't support them.
MAIN_AGENT_MODEL = "openai-responses:gpt-5.4"

# Sub-agent model (cheaper, no builtin tool dependency)
SUB_AGENT_MODEL = "anthropic:claude-haiku-4-5-20251001"

# Agent name used for conversation persistence
AGENT_NAME = "sernia"

# Workspace path: Railway volume mount or module-relative fallback.
WORKSPACE_PATH = Path(
    os.environ.get("WORKSPACE_PATH", Path(__file__).resolve().parent / "workspace")
)

# ClickUp
CLICKUP_TEAM_ID = "90131316997"
DEFAULT_CLICKUP_VIEW_ID = "2ky3xg85-573"  # Peppino View

# Trigger bot identity — used when the agent runs autonomously via APScheduler
# jobs (email checks, Zillow checks) or webhooks (inbound SMS).
# Not a real Clerk user; conversations use shared team access (clerk_user_id=None queries).
TRIGGER_BOT_ID = "system:sernia-ai"
TRIGGER_BOT_NAME = "Sernia AI (Trigger)"
# Google API delegation requires impersonating a real Google Workspace user.
GOOGLE_DELEGATION_EMAIL = "emilio@serniacapital.com"

# Scheduled check defaults — overridable via DB-backed schedule_config setting.
DEFAULT_SCHEDULE_DAYS_OF_WEEK = [0, 1, 2, 3, 4]  # Mon–Fri (APScheduler convention)
DEFAULT_SCHEDULE_HOURS = [8, 11, 14, 17]  # 8am, 11am, 2pm, 5pm ET

# Shared team contact ID in OpenPhone (Quo).
# Phone number is looked up at runtime via the API — not hardcoded.
QUO_SHARED_TEAM_CONTACT_ID = "699b78b18371c26349b453ab"

# Frontend base URL — environment-aware absolute URLs for deeplinks in SMS.
_RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
FRONTEND_BASE_URL = (
    "https://eesposito.com" if _RAILWAY_ENV == "production"
    else "https://dev.eesposito.com" if _RAILWAY_ENV == "development"
    else "http://localhost:5173"
)

# Quo (OpenPhone) phone IDs
# Sernia AI: internal-only line for messaging the team and shared number.
QUO_SERNIA_AI_PHONE_ID = "PNWvNqsFFy"
# Shared team number: the only line allowed to message external contacts.
QUO_SHARED_EXTERNAL_PHONE_ID = "PNpTZEJ7la"
# Company name used to distinguish internal vs external contacts.
QUO_INTERNAL_COMPANY = "Sernia Capital LLC"
# Internal email domain — emails to this domain bypass HITL approval.
INTERNAL_EMAIL_DOMAIN = "serniacapital.com"

# SMS length limits — AT&T rejects messages around 670 chars.
# Auto-split long messages into chunks at this threshold.
SMS_SPLIT_THRESHOLD = 500
# Hard reject at the tool level — LLM must shorten the message.
SMS_MAX_LENGTH = 1000

# AI SMS conversation: max messages to fetch from OpenPhone for initial bootstrap
SMS_CONVERSATION_MAX_MESSAGES = 20

# AI SMS history trimming — reduce context size for SMS-triggered runs.
# Default window: last N days OR last N user messages, whichever goes further back.
# The agent is told history was trimmed and can use tools to load more.
SMS_HISTORY_MIN_DAYS = 3
SMS_HISTORY_MIN_MESSAGES = 3

# ClickUp — Maintenance list for task creation from SMS triggers
CLICKUP_MAINTENANCE_LIST_ID = "901312027371"

# Shared external mailbox for outbound email and calendar invites.
# Using the shared mailbox ensures attendees receive email invites
# (self-organized events via delegation don't trigger notifications).
SHARED_EXTERNAL_EMAIL = "all@serniacapital.com"

# Contact slug for Emilio — used to look up clerk_user_id for targeted push notifications.
EMILIO_CONTACT_SLUG = "emilio"
