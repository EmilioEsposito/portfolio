"""
Input sanitization utilities for AI agent endpoints.

Protects against SSRF and other attacks by sanitizing user-provided message parts
before they reach AI agents.
"""
from typing import Any
import logfire


# Message part types that should be stripped for security
# document-url: Can be used for SSRF attacks (fetch arbitrary URLs)
# document-file: Could be used to leak file contents
BLOCKED_PART_TYPES = {"document-url", "document-file", "file"}


def sanitize_message_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove potentially dangerous message parts.

    Returns only text parts, stripping document-url, document-file, etc.
    that could be used for SSRF or other attacks.
    """
    safe_parts = []
    for part in parts:
        part_type = part.get("type", "")
        if part_type in BLOCKED_PART_TYPES:
            # Log security event for monitoring
            logfire.warn(
                "blocked dangerous message part",
                part_type=part_type,
                url=part.get("url", "")[:200] if part.get("url") else None,
            )
            continue
        safe_parts.append(part)
    return safe_parts


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sanitize all messages in a conversation, removing dangerous parts.

    This should be applied to incoming Vercel AI SDK messages before
    passing them to VercelAIAdapter or agents.
    """
    sanitized = []
    for msg in messages:
        parts = msg.get("parts", [])
        safe_parts = sanitize_message_parts(parts)

        # Only include message if it has any parts left
        if safe_parts:
            sanitized_msg = {**msg, "parts": safe_parts}
            sanitized.append(sanitized_msg)
        else:
            # Message had only dangerous parts - log and skip
            logfire.warn(
                "dropped message with only dangerous parts",
                message_role=msg.get("role"),
            )

    return sanitized


def sanitize_request_json(request_json: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize a full Vercel AI SDK request payload.

    Returns a copy with messages sanitized.
    """
    if "messages" not in request_json:
        return request_json

    return {
        **request_json,
        "messages": sanitize_messages(request_json["messages"]),
    }
