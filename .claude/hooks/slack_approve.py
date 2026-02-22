#!/usr/bin/env python3
"""Claude Code PermissionRequest hook — forwards approval requests to Slack.

Auto-detects whether you're at your laptop or away:
  - Active (idle < 2 min) → falls through to local CLI prompt
  - Idle   (idle >= 2 min) → sends to Slack for phone approval

Override with:  export CLAUDE_APPROVAL_MODE=slack   (always Slack)
                export CLAUDE_APPROVAL_MODE=local   (always local)
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def load_dotenv():
    """Load .env file from the project root (stdlib only, no python-dotenv)."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key not in os.environ:
            os.environ[key] = value


load_dotenv()

SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_CLAUDE_CODE", "")
APPROVAL_SERVER = os.environ.get("CLAUDE_APPROVAL_SERVER", "https://claude-approval-production.up.railway.app")
APPROVAL_MODE = os.environ.get("CLAUDE_APPROVAL_MODE", "auto")  # auto | slack | local
IDLE_THRESHOLD = int(os.environ.get("CLAUDE_IDLE_THRESHOLD", "120"))  # seconds
POLL_INTERVAL = 1  # seconds
POLL_TIMEOUT = 600  # 10 minutes


def fall_through():
    """Output empty JSON so Claude Code falls through to the normal user prompt."""
    sys.exit(2)


def get_idle_seconds() -> int:
    """Get macOS idle time in seconds (time since last keyboard/mouse input)."""
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "HIDIdleTime" in line:
                # Value is in nanoseconds
                ns = int(line.split()[-1])
                return ns // 1_000_000_000
    except Exception:
        pass
    return 0  # assume active if detection fails


def should_use_slack() -> bool:
    """Decide whether to route to Slack or fall through to local prompt."""
    if APPROVAL_MODE == "slack":
        return True
    if APPROVAL_MODE == "local":
        return False
    # auto: check idle time
    return get_idle_seconds() >= IDLE_THRESHOLD


def decide(behavior: str, message: str = ""):
    """Output a decision and exit."""
    decision = {"behavior": behavior}
    if message and behavior == "deny":
        decision["message"] = message
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }))
    sys.exit(0)


def send_slack_message(request_id: str, tool_name: str, tool_input: dict) -> bool:
    """Send approval request to Slack with interactive buttons."""
    input_preview = json.dumps(tool_input, indent=2)
    if len(input_preview) > 800:
        input_preview = input_preview[:800] + "\n…"

    message = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Claude Code Permission Request"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tool:*\n`{tool_name}`"},
                    {"type": "mrkdwn", "text": f"*ID:*\n`{request_id}`"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Input:*\n```{input_preview}```"},
            },
            {
                "type": "actions",
                "block_id": f"approval_{request_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve",
                        "value": request_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "action_id": "deny",
                        "value": request_id,
                    },
                ],
            },
        ],
    }

    data = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req)
        return True
    except urllib.error.URLError as e:
        print(f"Failed to send Slack message: {e}", file=sys.stderr)
        return False


def poll_for_decision(request_id: str) -> str | None:
    """Poll the approval server until a decision arrives or we time out."""
    url = f"{APPROVAL_SERVER}/decision/{request_id}"

    for _ in range(POLL_TIMEOUT // POLL_INTERVAL):
        try:
            resp = urllib.request.urlopen(url)
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("decision")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                time.sleep(POLL_INTERVAL)
                continue
            time.sleep(POLL_INTERVAL)
        except urllib.error.URLError:
            time.sleep(POLL_INTERVAL)

    return None  # timed out


def main():
    payload = json.load(sys.stdin)

    tool_name = payload.get("tool_name", "unknown")
    tool_input = payload.get("tool_input", {})
    request_id = uuid.uuid4().hex[:8]

    # No webhook or not using Slack → fall through to local prompt
    if not SLACK_WEBHOOK or not should_use_slack():
        fall_through()

    # Register the request with the approval server
    try:
        body = json.dumps({
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{APPROVAL_SERVER}/register",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req)
    except urllib.error.URLError:
        # Server not running → fall through to local prompt
        fall_through()

    # Send Slack message
    if not send_slack_message(request_id, tool_name, tool_input):
        fall_through()

    # Poll for decision
    decision = poll_for_decision(request_id)

    if decision in ("allow", "deny"):
        decide(decision)
    else:
        decide("deny", "Timed out waiting for Slack approval")


if __name__ == "__main__":
    main()
