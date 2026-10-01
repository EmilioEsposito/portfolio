"""Hosted deployments retain automation; local development requires explicit opt-in."""

import os


def background_automation_enabled() -> bool:
    value = os.getenv("BACKGROUND_AUTOMATION_ENABLED")
    if value is None:
        return bool(os.getenv("RAILWAY_ENVIRONMENT_NAME"))
    if value.lower() not in {"true", "false", "1", "0"}:
        raise ValueError("BACKGROUND_AUTOMATION_ENABLED must be true, false, 1, or 0")
    return value.lower() in {"true", "1"}
