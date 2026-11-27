"""Approve / Reject cards for actions that reach people outside the team or cannot be undone.

How a card works:

  1. The model calls an entry tool (``quo_send_sms``). The tool checks the
     request, keeps it in ``_PENDING`` under a random single-use id, and
     answers with a card. Nothing has been sent.
  2. The person clicks Approve or Reject. The card calls a confirm tool
     (``_confirm_send_sms``) with that id and the decision.
  3. The confirm tool takes the request out of ``_PENDING`` and carries out
     the decision, once.

What keeps the model away from the confirm tools is the client. They are
marked ``visibility: ["app"]``, and a client that implements MCP Apps shows
such a tool to the card and never to the model. A client can only forward a
card's call to a tool it was given in ``tools/list``, so the confirm tools
have to be listed. They are listed for the clients that declare the MCP Apps
extension, which is how a client says it draws cards and honours that
marking. Every other client is not told they exist, and the entry tools
refuse it before anything is queued, because nobody could click.

A confirm tool does nothing without a request an entry tool queued: it needs
that request's random id, which only the card holds, and the same signed-in
user who asked. So a call to one is not checked against the client again. The
click a client forwards for a card is trusted to be that client's, and a
client that did not say so on that one call must not lose the click.

This is a client's promise plus this server's checks (single use, expiry,
the same signed-in user). It is not proof of a click: a client that declares
the extension and then hands the confirm tools to its model could approve its
own request. Anything that must hold against that needs an approval outside
the chat.

The check reads what the client declared on the request itself, which the
2026-07-28 protocol carries on every request. This service is stateless, so
an older client, which declares only once when it connects, is treated as one
that cannot show a card.

From the FastMCP 4 upgrade until this was fixed, the confirm tools were kept
out of ``tools/list`` for every client. That also hid them from the client
that had to forward the click, so no Approve reached this server. A test now
asserts that every button on a card calls a listed tool.

``_PENDING`` is in memory: a restart loses what is waiting, and the card then
says the request is unknown. A request older than ``_PENDING_TTL_SECONDS`` is
refused.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence

import logfire
from fastmcp import FastMCPApp
from fastmcp.apps.config import UI_EXTENSION_ID
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.tools import Tool
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

from sernia_mcp.config import INTERNAL_EMAIL_DOMAIN, local_auth_bypass_enabled
from sernia_mcp.core.errors import CoreError, NotFoundError, ValidationError
from sernia_mcp.core.google.gmail import send_email_core
from sernia_mcp.core.quo import tasks
from sernia_mcp.core.quo.send_sms import (
    SMS_SPLIT_THRESHOLD,
    resolve_group_sms_routing_core,
    resolve_sms_routing_core,
    send_group_sms_core,
    send_sms_core,
)
from sernia_mcp.identity import resolve_user_email_for_request
from sernia_mcp.tools.quo import task_client

# Requests waiting for a decision, by id. Each is taken out when decided.
_PENDING: dict[str, dict] = {}
_PENDING_TTL_SECONDS = 600  # 10 minutes


def client_draws_cards() -> bool:
    """Whether the client behind this request declared the MCP Apps extension."""
    try:
        return get_context().client_supports_extension(UI_EXTENSION_ID)
    except RuntimeError:  # not inside a request
        return False


def _is_app_only(tool: Tool) -> bool:
    ui = (tool.meta or {}).get("ui", {})
    return ui.get("visibility") == ["app"]


class ApprovalApp(FastMCPApp):
    """List the confirm tools only for clients that draw cards.

    FastMCP lists app-only tools for every client and leaves hiding them from
    the model to the client. A client that never heard of MCP Apps would hand
    them to its model as ordinary tools, so for those clients they are left
    out of the listing.
    """

    async def _list_tools(self) -> Sequence[Tool]:
        tools = await super()._list_tools()
        draws = client_draws_cards()
        logfire.debug("approval cards: client draws cards = {draws}", draws=draws)
        return tools if draws else [tool for tool in tools if not _is_app_only(tool)]


approvals_app = ApprovalApp("sernia_approvals")


def _require_cards(doing: str) -> None:
    """Refuse an entry tool for a client that cannot show its card."""
    if not client_draws_cards():
        raise ToolError(
            f"{doing} needs a person to click Approve on a card, and this client cannot "
            "show one. Nothing was queued. Ask the person to do it from Claude on the web, "
            "desktop or phone, or through Sernia AI in the team app."
        )


def _signed_in_user() -> str:
    """Who is asking or deciding: their email, or "local" with auth switched off for development."""
    token = get_access_token()
    if token is None and local_auth_bypass_enabled():
        return "local"
    if token and (email := (token.claims or {}).get("email")):
        return str(email).strip().lower()
    raise ToolError("Sign in to ask for or decide an approval.")


def _queue(kind: str, **request: object) -> str:
    """Keep a request, under the user who asked, until they decide it. Returns its single-use id."""
    pending_id = uuid.uuid4().hex
    _PENDING[pending_id] = {
        "type": kind,
        "owner": _signed_in_user(),
        "created_at": time.time(),
        **request,
    }
    return pending_id


def _take(pending_id: str, kind: str, what: str) -> dict:
    """The request a decision is for, taken out so it is decided once.

    Raises when there is none for this user to decide: unknown, decided
    already, another user's (left in place for its owner), or expired.
    """
    record = _PENDING.get(pending_id)
    if not record or record["type"] != kind:
        raise ToolError(f"This {what} is unknown or already decided. Ask for a new card.")
    if record["owner"] != _signed_in_user():
        raise ToolError(f"This {what} was requested by another user.")
    del _PENDING[pending_id]
    age = time.time() - record["created_at"]
    if age > _PENDING_TTL_SECONDS:
        raise ToolError(
            f"This {what} expired ({int(age)}s old; a card lasts {_PENDING_TTL_SECONDS}s). "
            "Ask for a new card."
        )
    return record


def _decision_buttons(
    confirm_tool: str,
    pending_id: str,
    *,
    approve: str,
    done: str,
    failed: str,
    approve_variant: str = "success",
    reject: str = "Reject",
    reject_variant: str = "destructive",
) -> None:
    """The two buttons of a card. Call inside its ``CardFooter``.

    A click disables both buttons, then calls the confirm tool. The toast
    reports what came back: ``done`` only when the confirm tool returned, and
    ``failed`` with the reason under it when it raised or the call did not
    arrive. ``failed`` must hold whether or not anything went out, since a
    long text can fail after its first part.

    The reason is a toast description of its own. Prefab fills ``$error`` only
    in a string that is nothing but that template; mixed into other words it
    comes out empty.
    """

    def click(decision: str, toast: ShowToast) -> list:
        return [
            SetState("decided", True),
            CallTool(
                confirm_tool,
                arguments={"pending_id": pending_id, "decision": decision},
                on_success=toast,
                on_error=ShowToast(failed, description="{{ $error }}", variant="error"),
            ),
        ]

    with Row(gap=2):
        Button(
            approve,
            variant=approve_variant,
            on_click=click("approve", ShowToast(done, variant="success")),
            disabled=Rx("decided"),
        )
        Button(
            reject,
            variant=reject_variant,
            on_click=click("reject", ShowToast("Cancelled", variant="warning")),
            disabled=Rx("decided"),
        )


# -----------------------------------------------------------------------------
# SMS
# -----------------------------------------------------------------------------


@approvals_app.ui()
async def quo_send_sms(to_phone: str | list[str], message: str) -> PrefabApp:
    """Send an SMS to one Quo contact, or a GROUP text to 2-10 contacts.

    Returns an Approve / Reject card — the SMS is NOT sent until the user
    clicks Approve in the card. Only MCP clients that can show the card
    (Claude on the web, desktop or phone) can send; any other client is
    refused and nothing is queued.

    **Group texts**: pass a list of 2-10 phone numbers to start ONE shared
    thread — all recipients see the message and each other's replies (and
    each other's phone numbers). Deterministic gates that approval cannot
    override: every number must be a Quo contact, internal team members and
    external contacts can NEVER be mixed in one group, and tenants from
    DIFFERENT units are always blocked from sharing a group thread
    (roommates in the same unit are fine). To message multiple people
    SEPARATELY (private 1:1 threads), call this tool once per recipient
    instead.

    Args:
        to_phone: Recipient phone in E.164 format (e.g. "+14155550100"),
            or a list of 2-10 numbers for a group text (one shared thread).
        message: SMS body (max 1000 chars; auto-split above 500).
    """
    _require_cards("Sending a text")
    if isinstance(to_phone, list) and len(to_phone) == 1:
        to_phone = to_phone[0]

    if len(message) > 1000:
        raise ToolError(f"message is {len(message)} chars, max 1000")

    if isinstance(to_phone, list):
        try:
            group = await resolve_group_sms_routing_core(to_phone)
        except (NotFoundError, ValidationError) as e:
            raise ToolError(str(e)) from e
        except CoreError as e:
            raise ToolError(f"quo_send_sms failed: {e}") from e
        pending_id = _queue(
            "sms",
            group=True,
            to_phones=group.phones,
            message=message,
            contact_name=", ".join(group.recipient_names),
        )
        title = f"Send GROUP SMS to {len(group.phones)} recipients?"
        recipients = ", ".join(
            f"{name} ({phone})"
            for name, phone in zip(group.recipient_names, group.phones, strict=True)
        )
        to_line = f"**To (one shared thread):** {recipients}"
        internal, line_name, done = group.all_internal, group.line_name, "Group SMS sent"
    else:
        try:
            routing = await resolve_sms_routing_core(to_phone)
        except NotFoundError as e:
            raise ToolError(str(e)) from e
        except CoreError as e:
            raise ToolError(f"quo_send_sms failed: {e}") from e
        pending_id = _queue(
            "sms",
            to_phone=to_phone,
            message=message,
            contact_name=routing.contact_name,
            is_internal=routing.is_internal,
        )
        title = f"Send SMS to {routing.contact_name or to_phone}?"
        to_line = f"**To:** {routing.contact_name or '(no name)'} — {to_phone}"
        internal, line_name, done = routing.is_internal, routing.line_name, "SMS sent"

    with Card(css_class="max-w-xl") as view:
        with CardHeader():
            CardTitle(title)
        with CardContent():
            with Column(gap=3):
                Text(to_line)
                Text(f"**Routing:** {'INTERNAL' if internal else 'EXTERNAL'} line ({line_name})")
                Heading("Message", level=4)
                Text(message)
        with CardFooter():
            _decision_buttons(
                "_confirm_send_sms",
                pending_id,
                approve="Approve & Send",
                done=done,
                failed="The send did not complete",
            )
    return PrefabApp(view=view, state={"decided": False})


@approvals_app.tool()
async def _confirm_send_sms(pending_id: str, decision: str) -> str:
    """Called only by the SMS card's buttons. Raises unless the decision was carried out."""
    request = _take(pending_id, "sms", "text message")
    is_group = request.get("group", False)
    recipient = request.get("contact_name") or (
        ", ".join(request["to_phones"]) if is_group else request["to_phone"]
    )
    if decision != "approve":
        return f"Cancelled SMS to {recipient} (decision={decision!r})."
    try:
        if is_group:
            result = await send_group_sms_core(request["to_phones"], request["message"])
        else:
            result = await send_sms_core(request["to_phone"], request["message"])
    except CoreError as e:
        # A long text goes out in parts, and the parts before the one that
        # failed were delivered. Say so, or a second try sends them twice.
        in_parts = len(request["message"]) > SMS_SPLIT_THRESHOLD
        raise ToolError(
            f"The send failed: {e}"
            + (
                " This text goes out in parts, and any part before the one named was sent. "
                "Read the thread before sending again."
                if in_parts
                else ""
            )
        ) from e
    kind = "Group SMS (one shared thread)" if is_group else "SMS"
    return (
        f"{kind} sent to {result.contact_name or result.to_phone} via {result.line_name} "
        f"({result.parts_sent} part{'s' if result.parts_sent != 1 else ''})."
    )


# -----------------------------------------------------------------------------
# Email
# -----------------------------------------------------------------------------


@approvals_app.ui()
async def google_send_email(to: list[str], subject: str, body: str) -> PrefabApp:
    """Send an email (internal or external recipients).

    Returns an Approve / Reject card — the email is NOT sent until the user
    clicks Approve. Only MCP clients that can show the card (Claude on the
    web, desktop or phone) can send; any other client is refused and nothing
    is queued.

    Args:
        to: List of recipient email addresses.
        subject: Email subject.
        body: Plain-text body.
    """
    _require_cards("Sending an email")
    if not to:
        raise ToolError("to[] is empty")

    all_internal = all(addr.strip().lower().endswith(f"@{INTERNAL_EMAIL_DOMAIN}") for addr in to)
    pending_id = _queue("email", to=list(to), subject=subject, body=body, all_internal=all_internal)

    audience = f"INTERNAL (all @{INTERNAL_EMAIL_DOMAIN})" if all_internal else "EXTERNAL"
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
            _decision_buttons(
                "_confirm_send_email",
                pending_id,
                approve="Approve & Send",
                done="Email sent",
                failed="The send did not complete",
            )
    return PrefabApp(view=view, state={"decided": False})


@approvals_app.tool()
async def _confirm_send_email(pending_id: str, decision: str) -> str:
    """Called only by the email card's buttons. Raises unless the decision was carried out."""
    request = _take(pending_id, "email", "email")
    if decision != "approve":
        return f"Cancelled email {request['subject']!r} (decision={decision!r})."
    try:
        result = await send_email_core(
            to=request["to"],
            subject=request["subject"],
            body=request["body"],
            user_email=resolve_user_email_for_request(),
        )
    except CoreError as e:
        raise ToolError(f"The send failed: {e}") from e
    return f"Email sent to {', '.join(result.to)} (message_id={result.message_id})."


# -----------------------------------------------------------------------------
# Quo task deletion
# -----------------------------------------------------------------------------


@approvals_app.ui()
async def quo_delete_task(task_id: tasks.TaskId) -> PrefabApp:
    """Request approval to delete a Quo task. The card shows its current title and TK ID."""
    _require_cards("Deleting a Quo task")
    _signed_in_user()  # refused before the task is looked up
    async with task_client() as client:
        task = await tasks.get_task(client, task_id)
    pending_id = _queue("quo_delete_task", task_id=task_id)
    with Card(css_class="max-w-xl") as view:
        with CardHeader():
            CardTitle("Delete this Quo task?")
        with CardContent():
            with Column(gap=2):
                Text(task.get("title") or "(Untitled task)")
                Text(task_id)
        with CardFooter():
            _decision_buttons(
                "_confirm_delete_quo_task",
                pending_id,
                approve="Delete task",
                approve_variant="destructive",
                done="Task deleted",
                failed="The deletion was not confirmed",
                reject="Cancel",
                reject_variant="secondary",
            )
    return PrefabApp(view=view, state={"decided": False})


@approvals_app.tool()
async def _confirm_delete_quo_task(pending_id: str, decision: str) -> str:
    """Called only by the deletion card's buttons. Applies the decision once."""
    request = _take(pending_id, "quo_delete_task", "task deletion")
    if decision != "approve":
        return "Quo task deletion cancelled."
    async with task_client() as client:
        return await tasks.delete_task(client, request["task_id"])
