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

# Quo (OpenPhone) phone IDs
# Sernia AI: internal-only line for messaging the team and shared number.
QUO_SERNIA_AI_PHONE_ID = "PNWvNqsFFy"
# Shared team number: the only line allowed to message external contacts.
QUO_SHARED_EXTERNAL_PHONE_ID = "PNpTZEJ7la"
# Company name used to distinguish internal vs external contacts.
QUO_INTERNAL_COMPANY = "Sernia Capital LLC"
