"""Local dev harness for exercising the Apps-based approval flow in a browser.

Run::

    SERNIA_MCP_DISABLE_AUTH=true uv run fastmcp dev apps src/sernia_mcp/dev_server.py

What this does:

  * Spawns the MCP server on :8000 and the FastMCP dev UI on :8080.
  * Browser opens automatically. The picker lists the ``@app.ui()`` tools
    (``quo_delete_task``, ``quo_send_sms``, ``google_send_email``, ``showing_book``
    and ``showing_cancel``). Pick one, fill in the form,
    launch.
  * The approval card renders. Click Approve or Reject; the MCP Log panel
    shows the ``tools/call _confirm_send_*`` traffic and the card shows a
    toast with the outcome.

Quo task lookups, deletions, upstream sends, and the showing API are MOCKED. Nothing
hits OpenPhone, Gmail or the booking service — safe to exercise with any phone/email. Mocks print to stdout so you can confirm what
*would* have happened.

This file is a dev harness ONLY. Not wired into the production server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP

from sernia_mcp.core import showing_bookings as showing_core
from sernia_mcp.core.types import EmailSendResult, GroupSmsRouting, SmsResult, SmsRouting
from sernia_mcp.tools import approvals
from sernia_mcp.tools import showing_bookings as showing_tools

# -----------------------------------------------------------------------------
# Mocks — replace upstream core calls with harmless stubs.
# `approvals` holds module-level references to these names; Python resolves
# them at call time, so rebinding on the module captures calls at runtime.
# -----------------------------------------------------------------------------


async def _fake_resolve_sms_routing_core(to_phone: str) -> SmsRouting:
    """Pretend every phone resolves. Mark numbers starting with ``+1555`` as
    internal so you can test both branches of the routing message."""
    is_internal = to_phone.startswith("+1555")
    return SmsRouting(
        contact_id="dev-contact",
        contact_name=f"Dev Contact {to_phone[-4:]}",
        is_internal=is_internal,
        from_phone_id="DEVPHONEID",
        line_name="Sernia AI (dev)" if is_internal else "Sernia Team (dev)",
    )


async def _fake_send_sms_core(to_phone: str, message: str, *, routing=None) -> SmsResult:
    print(f"[DEV MOCK send_sms_core] would send to={to_phone!r} ({len(message)} chars)")
    return SmsResult(
        to_phone=to_phone,
        contact_name=(routing.contact_name if routing else None),
        line_name=(routing.line_name if routing else "(dev)"),
        parts_sent=1,
        message_chars=len(message),
    )


async def _fake_resolve_group_sms_routing_core(phones: list[str]) -> GroupSmsRouting:
    """Pretend every number in a group resolves, internal when all start with ``+1555``."""
    all_internal = all(phone.startswith("+1555") for phone in phones)
    return GroupSmsRouting(
        phones=list(dict.fromkeys(phones)),
        recipient_names=[f"Dev Contact {phone[-4:]}" for phone in dict.fromkeys(phones)],
        all_internal=all_internal,
        from_phone_id="DEVPHONEID",
        line_name="Sernia AI (dev)" if all_internal else "Sernia Team (dev)",
    )


async def _fake_send_group_sms_core(to_phones: list[str], message: str) -> SmsResult:
    print(f"[DEV MOCK send_group_sms_core] would send to={to_phones!r} ({len(message)} chars)")
    return SmsResult(
        to_phone=", ".join(to_phones),
        contact_name=", ".join(f"Dev Contact {phone[-4:]}" for phone in to_phones),
        line_name="(dev)",
        parts_sent=1,
        message_chars=len(message),
    )


async def _fake_send_email_core(
    to: list[str],
    subject: str,
    body: str,
    *,
    user_email: str,
    sender_override: str | None = None,
) -> EmailSendResult:
    print(f"[DEV MOCK send_email_core] would send to={to!r} subject={subject!r}")
    return EmailSendResult(
        to=list(to),
        subject=subject,
        from_address=sender_override or user_email,
        message_id="DEV-MOCK-MSG-ID",
    )


async def _fake_preview_booking(token: str, booking: dict) -> str:
    return (
        f"Books {booking['name']} into a new showing of 12 Example St (unit {booking['unit']}) "
        "on Wed, Apr 14 at 4:00 PM, hosted by Dev Agent. Dev Agent gets a calendar event; the "
        "unit's residents get a text about the showing, if anyone lives there. (dev mock)"
    )


async def _fake_book(token: str, booking: dict, effect: str) -> str:
    print(f"[DEV MOCK showing book] would book {booking!r}")
    return f"Booked {booking['name']} at {booking['unit']} (dev mock)."


async def _fake_preview_cancellation(token: str, showing_id: int, attendee_id: int | None) -> str:
    who = "everyone booked in it" if attendee_id is None else f"attendee {attendee_id}"
    return f"Cancels showing {showing_id} for {who}. (dev mock)"


async def _fake_cancel(token: str, showing_id: int, attendee_id: int | None, effect: str) -> str:
    print(f"[DEV MOCK showing cancel] would cancel showing={showing_id} attendee={attendee_id}")
    return f"Cancelled showing {showing_id} (dev mock)."


@asynccontextmanager
async def _fake_task_client():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"title": "Demo follow-up"}})
        return httpx.Response(204)

    async with httpx.AsyncClient(
        base_url="https://quo.test", transport=httpx.MockTransport(respond)
    ) as client:
        yield client


# FastMCP's dev UI draws cards but does not declare the MCP Apps extension, so
# the check that keeps confirm tools from clients without cards would refuse
# it. Here every client is the dev UI.
approvals.client_draws_cards = lambda: True
approvals.task_client = _fake_task_client
approvals.resolve_sms_routing_core = _fake_resolve_sms_routing_core  # type: ignore[assignment]
approvals.send_sms_core = _fake_send_sms_core  # type: ignore[assignment]
# Group texts too: left real, an Approve here would text real people.
approvals.resolve_group_sms_routing_core = _fake_resolve_group_sms_routing_core  # type: ignore[assignment]
approvals.send_group_sms_core = _fake_send_group_sms_core  # type: ignore[assignment]
approvals.send_email_core = _fake_send_email_core  # type: ignore[assignment]
# Showings: left real, an Approve here would book or cancel through the API.
showing_tools._caller = lambda: "dev-token"  # type: ignore[assignment]
showing_core.preview_booking = _fake_preview_booking  # type: ignore[assignment]
showing_core.book = _fake_book  # type: ignore[assignment]
showing_core.preview_cancellation = _fake_preview_cancellation  # type: ignore[assignment]
showing_core.cancel = _fake_cancel  # type: ignore[assignment]


mcp = FastMCP(
    "sernia-mcp-dev",
    instructions=(
        "Local dev harness. Upstream Quo/Gmail calls and the showing API are mocked. "
        "Use any phone/email — nothing is actually sent."
    ),
)
mcp.add_provider(approvals.approvals_app)
