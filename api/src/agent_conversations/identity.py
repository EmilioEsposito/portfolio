"""Stable persistence identities for Sernia conversations and automated runs.

Both agents share the team conversation store. These values identify stored
records; changing the Python package does not change their ownership.
"""

# Agent name used for conversation persistence
AGENT_NAME = "sernia"

# Trigger bot identity — used when the agent runs autonomously via APScheduler
# jobs (email checks, Zillow checks) or webhooks (inbound SMS).
# Not a real Clerk user; conversations use shared team access (clerk_user_id=None queries).
TRIGGER_BOT_ID = "system:sernia-ai"

TRIGGER_BOT_NAME = "Sernia AI (Trigger)"
