"""
Local dev harness for exercising the Apps-based approval flow in a browser.

Run:
    fastmcp dev apps api/src/sernia_mcp/dev_server.py

What this does:
  * Spawns the MCP server on :8000 and the FastMCP dev UI on :8080.
  * Browser opens automatically. The picker lists the @app.ui() tools
    (quo_send_sms, google_send_email). Pick one, fill in the form, launch.
  * The approval card renders in the dev UI. Click Approve or Reject and
    watch the inspector panel for the CallTool → _confirm_send_* traffic.

All upstream sends are MOCKED. Nothing hits OpenPhone or Gmail — this is
safe to exercise with any phone number or email, real or fake. The mocks
print a line to stdout so you can confirm what would have happened.

This file is a dev harness ONLY. Do not import from it in production code.
It is NOT wired into api/index.py.
"""
from api.src.sernia_mcp.tools import approvals
from api.src.tool_core.types import EmailSendResult, SmsResult, SmsRouting
from fastmcp import FastMCP


# -----------------------------------------------------------------------------
# Mocks — replace upstream tool_core calls with harmless stubs.
# approvals.* holds module-level references to these names; Python resolves
# them at call time, so rebinding on the module works.
# -----------------------------------------------------------------------------

async def _fake_resolve_sms_routing_core(to_phone: str) -> SmsRouting:
    """Pretend every phone resolves. Mark numbers starting with '+1555' as
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


# Rebind module-level refs in approvals.* — all the tool code reaches these
# by module lookup, so this actually captures calls at runtime.
approvals.resolve_sms_routing_core = _fake_resolve_sms_routing_core  # type: ignore[assignment]
approvals.send_sms_core = _fake_send_sms_core  # type: ignore[assignment]
approvals.send_email_core = _fake_send_email_core  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# Dev server — only the approvals provider; no read tools, no auth.
# -----------------------------------------------------------------------------

mcp = FastMCP(
    "sernia-mcp-dev",
    instructions=(
        "Local dev harness. Upstream Quo/Gmail calls are mocked. "
        "Use any phone/email — nothing is actually sent."
    ),
)
mcp.add_provider(approvals.approvals_app)
