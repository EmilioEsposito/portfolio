"""
Configuration for the Sernia AI agent.

Keep tunables here so they're easy to find and tweak.
"""
import os
from pathlib import Path

# Web search / fetch: only these domains are allowed.
# Used by WebSearchTool and WebFetchTool (Anthropic builtin tools).
WEB_SEARCH_ALLOWED_DOMAINS: list[str] = [
    "zillow.com",
    "redfin.com",
    # "realtor.com",  # Blocks Anthropic's crawler
    "apartments.com",
    "rentometer.com",
    "clickup.com",
    "serniacapital.com",
    # Add more as needed
]

# Compaction: trigger at ~85% of context window token estimate.
# Claude Sonnet 4.5 has a 200k context window.
TOKEN_COMPACTION_THRESHOLD = 170_000

# Summarization: tool results larger than this (chars) get summarized by the sub-agent.
SUMMARIZATION_CHAR_THRESHOLD = 10_000

# Main agent model.
# Anthropic required for WebSearchTool (allowed_domains) and WebFetchTool.
MAIN_AGENT_MODEL = "anthropic:claude-sonnet-4-6"

# Sub-agent model (cheaper, no builtin tool dependency)
SUB_AGENT_MODEL = "anthropic:claude-haiku-4-5-20251001"

# Agent name used for conversation persistence
AGENT_NAME = "sernia"

# Workspace path: Railway volume mount (/.workspace) or repo-relative fallback.
WORKSPACE_PATH = Path(
    os.environ.get("WORKSPACE_PATH", Path(__file__).resolve().parents[3] / ".workspace")
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

# Trigger schedule intervals — used by both the APScheduler cron/interval
# config and the email search lookback window in trigger prompts.
GENERAL_EMAIL_CHECK_INTERVAL_MINUTES = 180  # 3 hours
ZILLOW_EMAIL_CHECK_INTERVAL_HOURS = 3 # starts at 8am ET (13:00 UTC), ends at 5pm ET (22:00 UTC)

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

# AI SMS conversation: max messages to fetch from OpenPhone for initial bootstrap
SMS_CONVERSATION_MAX_MESSAGES = 20

# ClickUp — Maintenance list for task creation from SMS triggers
CLICKUP_MAINTENANCE_LIST_ID = "901312027371"

# Trigger schedule intervals — SMS inbox review
SMS_INBOX_CHECK_INTERVAL_HOURS = 3

# Shared external mailbox for outbound email and calendar invites.
# Using the shared mailbox ensures attendees receive email invites
# (self-organized events via delegation don't trigger notifications).
SHARED_EXTERNAL_EMAIL = "all@serniacapital.com"

# Contact slug for Emilio — used to look up clerk_user_id for targeted push notifications.
EMILIO_CONTACT_SLUG = "emilio"
