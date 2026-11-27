"""Local dev harness for exercising the Apps-based approval flow in a browser.

Run::

    SERNIA_MCP_DISABLE_AUTH=true uv run fastmcp dev apps src/sernia_mcp/dev_server.py

What this does:

  * Spawns the MCP server on :8000 and the FastMCP dev UI on :8080.
  * Browser opens automatically. The picker lists the ``@app.ui()`` tools
    (including ``quo_delete_task``, ``quo_send_sms``, and ``google_send_email``). Pick one, fill in the form,
    launch.
  * The approval card renders. Click Approve or Reject; the MCP Log panel
    shows the ``tools/call _confirm_send_*`` traffic and the card shows a
    toast with the outcome.

Quo task lookups, deletions, and upstream sends are MOCKED. Nothing hits OpenPhone or Gmail — safe to
exercise with any phone/email. Mocks print to stdout so you can confirm what
*would* have happened.

This file is a dev harness ONLY. Not wired into the production server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastmcp import FastMCP

from sernia_mcp.core.types import EmailSendResult, SmsResult, SmsRouting
from sernia_mcp.tools import approvals

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
approvals.send_email_core = _fake_send_email_core  # type: ignore[assignment]


mcp = FastMCP(
    "sernia-mcp-dev",
    instructions=(
        "Local dev harness. Upstream Quo/Gmail calls are mocked. "
        "Use any phone/email — nothing is actually sent."
    ),
)
mcp.add_provider(approvals.approvals_app)
