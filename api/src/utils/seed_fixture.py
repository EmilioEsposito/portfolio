"""Sanitization helpers for the seed fixture pipeline.

The fixture is a JSON export of recent Sernia conversations with PII redacted
and oversized tool results truncated. It is gitignored (this repo is public)
and distributed via the private Railway seed bucket — see
``api/src/utils/seed_bucket.py`` for storage and README.md ("Sanitized Seed
Data") for the human workflow.
"""
import hashlib
import json
import re
from pathlib import Path

FIXTURE_LOCAL_PATH = Path("api/seed_fixtures/agent_conversations.json")

# Tool results larger than this (chars) are truncated in the fixture. Giant
# tool dumps (sheets, full email bodies) are both the bulk of the file size
# and the hardest content to review for sensitive business details — an
# unreviewable fixture defeats the point of the review step.
TOOL_RESULT_CAP = 2_000
# Safety net for any other long string (user prompts, model text).
ANY_STRING_CAP = 8_000
_TRUNCATION_MARKER = "\n…[truncated for seed fixture]"

# Phone numbers: +1 412 555 0100 / (412) 555-0100 / 4125550100 / +14125550100
_PHONE_RE = re.compile(r"\+?1?[\s.\-(]*\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4}\b")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def digest(value: str, n: int = 7) -> str:
    """Deterministic short numeric digest so the same input maps to the same fake."""
    return str(int(hashlib.sha256(value.encode()).hexdigest(), 16))[-n:]


def _redact_phone(match: re.Match) -> str:
    # Hash the last 10 digits so "+14125550187" and "(412) 555-0187" map to
    # the same fake number — threads must stay internally consistent.
    digits = re.sub(r"\D", "", match.group(0))[-10:]
    return f"+1555{digest(digits)}"


def _redact_email(match: re.Match) -> str:
    domain = match.group(1).lower()
    if domain == "serniacapital.com":
        # Keep the internal domain — internal/external routing logic depends on it.
        return f"internal-{digest(match.group(0))}@serniacapital.com"
    return f"user-{digest(match.group(0))}@example.com"


def sanitize_text(text: str) -> str:
    text = _PHONE_RE.sub(_redact_phone, text)
    text = _EMAIL_RE.sub(_redact_email, text)
    return text


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + _TRUNCATION_MARKER


def sanitize_value(value, *, _in_tool_return: bool = False):
    """Recursively sanitize strings inside any JSON-ish structure.

    Inside tool-return parts, strings are also truncated to TOOL_RESULT_CAP;
    structured (dict/list) tool-return content that serializes too large is
    collapsed to a truncated JSON string. Everything else gets the looser
    ANY_STRING_CAP.
    """
    if isinstance(value, str):
        cap = TOOL_RESULT_CAP if _in_tool_return else ANY_STRING_CAP
        return _truncate(sanitize_text(value), cap)
    if isinstance(value, list):
        return [sanitize_value(v, _in_tool_return=_in_tool_return) for v in value]
    if isinstance(value, dict):
        is_tool_return = value.get("part_kind") == "tool-return"
        out = {}
        for k, v in value.items():
            if is_tool_return and k == "content" and isinstance(v, (dict, list)):
                serialized = json.dumps(v, default=str)
                if len(serialized) > TOOL_RESULT_CAP:
                    out[k] = _truncate(sanitize_text(serialized), TOOL_RESULT_CAP)
                    continue
            out[k] = sanitize_value(
                v, _in_tool_return=_in_tool_return or (is_tool_return and k == "content")
            )
        return out
    return value
