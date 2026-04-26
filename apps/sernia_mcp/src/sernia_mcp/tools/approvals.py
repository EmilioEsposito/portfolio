"""Apps-based approval flow for the two external-write tools.

Pattern (deterministic server-side enforcement via tool-visibility split):

  1. Model calls ``@app.ui()`` entry-point tool (e.g. ``quo_send_sms``).
  2. Tool queues the send into an in-memory pending dict, keyed by UUID,
     and returns a PrefabApp approval card. No external API call yet.
  3. User clicks Approve → the card fires ``CallTool("_confirm_send_sms", ...)``
     which is a hidden ``@app.tool()`` — not exposed to the model at all.
  4. The confirm tool looks up the pending row and performs the real send.

The model has no path to ``_confirm_send_sms``:

  - It isn't in ``tools/list`` (visibility=["app"], hidden from the model).
  - Direct calls via ``tools/call`` raise ``Unknown tool`` (verified in tests).
  - Even if a caller knew the name, they'd need a valid UUID from the pending
    dict, which is only surfaced through the UI.

Clients that can't render the PrefabApp (raw MCP clients, terminals) receive
the structured PrefabApp payload but have no way to reach the confirm tool —
the send cannot happen. Enforcement is structural, not capability-based.

State (``_PENDING``) is in-memory; restart loses pending approvals. Each row
carries a timestamp; rows older than ``_PENDING_TTL_SECONDS`` are rejected on
confirmation. DB-backed durability is a later concern.
"""
from __future__ import annotations

import time
import uuid

from fastmcp import FastMCPApp
from fastmcp.exceptions import ToolError
from prefab_ui import PrefabApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.components import (
    Button,
    Card,
    CardContent,
    CardFooter,
    CardHeader,
    CardTitle,
    Column,
    Heading,
    Row,
    Text,
)
from prefab_ui.rx import Rx

from sernia_mcp.config import INTERNAL_EMAIL_DOMAIN
from sernia_mcp.core.errors import CoreError, NotFoundError
from sernia_mcp.core.google.gmail import send_email_core
from sernia_mcp.core.quo.send_sms import resolve_sms_routing_core, send_sms_core
from sernia_mcp.identity import resolve_user_email_for_request

# Pending sends, keyed by short UUID.
# Row shape: {"type": "sms"|"email", "created_at": float, ...params}
_PENDING: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 600  # 10 minutes


# =============================================================================
# Single FastMCPApp holding both approval flows.
# =============================================================================
approvals_app = FastMCPApp("sernia_approvals")


# -----------------------------------------------------------------------------
# SMS
# -----------------------------------------------------------------------------

@approvals_app.ui()
async def quo_send_sms(to_phone: str, message: str) -> PrefabApp:
    """Send an SMS to a Quo contact (internal or external).

    Returns an Approve / Reject card — the SMS is NOT sent until the user
    clicks Approve in the card. Only MCP clients that can render PrefabApps
    (Claude Desktop, Claude app, VS Code Copilot, fastmcp dev apps) can
    complete the flow.

    Args:
        to_phone: Recipient phone in E.164 format (e.g. "+14155550100").
        message: SMS body (max 1000 chars; auto-split above 500).
    """
    try:
        routing = await resolve_sms_routing_core(to_phone)
    except NotFoundError as e:
        raise ToolError(str(e)) from e
    except CoreError as e:
        raise ToolError(f"quo_send_sms failed: {e}") from e
    if len(message) > 1000:
        raise ToolError(f"message is {len(message)} chars, max 1000")

    pid = uuid.uuid4().hex[:12]
    _PENDING[pid] = {
        "type": "sms",
        "to_phone": to_phone,
        "message": message,
        "contact_name": routing.contact_name,
        "is_internal": routing.is_internal,
        "created_at": time.time(),
    }

    audience = "INTERNAL" if routing.is_internal else "EXTERNAL"
    with Card(css_class="max-w-xl") as view:
        with CardHeader():
            CardTitle(f"Send SMS to {routing.contact_name or to_phone}?")
        with CardContent():
            with Column(gap=3):
                Text(f"**To:** {routing.contact_name or '(no name)'} — {to_phone}")
                Text(f"**Routing:** {audience} line ({routing.line_name})")
                Heading("Message", level=4)
                Text(message)
        with CardFooter():
            with Row(gap=2):
                Button(
                    "Approve & Send",
                    variant="success",
                    on_click=[
                        CallTool(
                            "_confirm_send_sms",
                            arguments={"pending_id": pid, "decision": "approve"},
                        ),
                        SetState("decided", True),
                        ShowToast("SMS sent", variant="success"),
                    ],
                    disabled=Rx("decided"),
                )
                Button(
                    "Reject",
                    variant="destructive",
                    on_click=[
                        CallTool(
                            "_confirm_send_sms",
                            arguments={"pending_id": pid, "decision": "reject"},
                        ),
                        SetState("decided", True),
                        ShowToast("Cancelled", variant="warning"),
                    ],
                    disabled=Rx("decided"),
                )
    return PrefabApp(view=view, state={"decided": False})


@approvals_app.tool()
async def _confirm_send_sms(pending_id: str, decision: str) -> str:
    """Hidden backend tool — called only by the approval card's buttons."""
    rec = _PENDING.pop(pending_id, None)
    if not rec or rec.get("type") != "sms":
        return f"Unknown pending SMS id={pending_id} (may have expired)."
    age = time.time() - rec.get("created_at", 0)
    if age > _PENDING_TTL_SECONDS:
        return f"Pending SMS expired ({int(age)}s old, TTL {_PENDING_TTL_SECONDS}s)."
    if decision != "approve":
        return (
            f"Cancelled SMS to {rec.get('contact_name') or rec['to_phone']} "
            f"(decision={decision!r})."
        )
    try:
        result = await send_sms_core(rec["to_phone"], rec["message"])
    except CoreError as e:
        return f"Send failed: {e}"
    return (
        f"SMS sent to {result.contact_name or result.to_phone} via {result.line_name} "
        f"({result.parts_sent} part{'s' if result.parts_sent != 1 else ''})."
    )


# -----------------------------------------------------------------------------
# Email
# -----------------------------------------------------------------------------

@approvals_app.ui()
async def google_send_email(to: list[str], subject: str, body: str) -> PrefabApp:
    """Send an email (internal or external recipients).

    Returns an Approve / Reject card — the email is NOT sent until the user
    clicks Approve. Only MCP clients that can render PrefabApps can complete
    the flow.

    Args:
        to: List of recipient email addresses.
        subject: Email subject.
        body: Plain-text body.
    """
    if not to:
        raise ToolError("to[] is empty")

    pid = uuid.uuid4().hex[:12]
    all_internal = all(
        addr.strip().lower().endswith(f"@{INTERNAL_EMAIL_DOMAIN}") for addr in to
    )
    _PENDING[pid] = {
        "type": "email",
        "to": list(to),
        "subject": subject,
        "body": body,
        "all_internal": all_internal,
        "created_at": time.time(),
    }

    audience = (
        f"INTERNAL (all @{INTERNAL_EMAIL_DOMAIN})" if all_internal else "EXTERNAL"
    )
    with Card(css_class="max-w-xl") as view:
        with CardHeader():
            CardTitle(f"Send email: {subject}")
        with CardContent():
            with Column(gap=3):
                Text(f"**To:** {', '.join(to)}")
                Text(f"**Routing:** {audience}")
                Heading("Body", level=4)
                Text(body)
        with CardFooter():
            with Row(gap=2):
                Button(
                    "Approve & Send",
                    variant="success",
                    on_click=[
                        CallTool(
                            "_confirm_send_email",
                            arguments={"pending_id": pid, "decision": "approve"},
                        ),
                        SetState("decided", True),
                        ShowToast("Email sent", variant="success"),
                    ],
                    disabled=Rx("decided"),
                )
                Button(
                    "Reject",
                    variant="destructive",
                    on_click=[
                        CallTool(
                            "_confirm_send_email",
                            arguments={"pending_id": pid, "decision": "reject"},
                        ),
                        SetState("decided", True),
                        ShowToast("Cancelled", variant="warning"),
                    ],
                    disabled=Rx("decided"),
                )
    return PrefabApp(view=view, state={"decided": False})


@approvals_app.tool()
async def _confirm_send_email(pending_id: str, decision: str) -> str:
    """Hidden backend tool — called only by the email approval card's buttons."""
    rec = _PENDING.pop(pending_id, None)
    if not rec or rec.get("type") != "email":
        return f"Unknown pending email id={pending_id} (may have expired)."
    age = time.time() - rec.get("created_at", 0)
    if age > _PENDING_TTL_SECONDS:
        return f"Pending email expired ({int(age)}s old, TTL {_PENDING_TTL_SECONDS}s)."
    if decision != "approve":
        return f"Cancelled email {rec['subject']!r} (decision={decision!r})."
    try:
        result = await send_email_core(
            to=rec["to"],
            subject=rec["subject"],
            body=rec["body"],
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        return f"Send failed: {e}"
    return f"Email sent to {', '.join(result.to)} (message_id={result.message_id})."
