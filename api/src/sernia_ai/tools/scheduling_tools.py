"""
Scheduling tools — schedule non-recurring SMS and email messages.

Uses APScheduler date trigger for one-time future delivery.
Routing (internal/external, phone ID, mailbox) is resolved at schedule time
using the same core logic as the send tools.
"""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import logfire
from apscheduler.jobstores.base import JobLookupError
from pydantic import EmailStr
from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext

from api.src.apscheduler_service.service import get_scheduler
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.tools.google_tools import (
    GMAIL_SCOPES,
    EmailRouting,
    _get_threading_headers,
    resolve_email_routing,
)
from api.src.sernia_ai.tools.quo_tools import (
    _build_quo_client,
    _seed_sms_conversation,
    execute_sms,
    resolve_sms_routing,
)

# Job ID prefixes — used to filter scheduled messages from system jobs
_SMS_JOB_PREFIX = "scheduled_sms_"
_EMAIL_JOB_PREFIX = "scheduled_email_"

scheduling_toolset = FunctionToolset[SerniaDeps]()


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------


def _parse_send_at(send_at: datetime, timezone: str) -> datetime | str:
    """Parse send_at + timezone into a timezone-aware datetime.

    Returns aware datetime on success, or an error string.
    """
    try:
        tz = ZoneInfo(timezone)
    except KeyError:
        return f"Invalid timezone: {timezone!r}. Use IANA format (e.g. 'America/New_York')."

    aware_dt = send_at.replace(tzinfo=tz) if send_at.tzinfo is None else send_at.astimezone(tz)

    if aware_dt <= datetime.now(tz):
        return f"send_at must be in the future. Got: {aware_dt.isoformat()}"

    return aware_dt


# ---------------------------------------------------------------------------
# Executor functions — called by APScheduler at the scheduled time.
# These run outside any agent context, so they create their own clients.
# ---------------------------------------------------------------------------


async def _execute_scheduled_sms(
    phone: str,
    message: str,
    from_phone_id: str,
    line_name: str,
    context: str = "",
) -> None:
    """Execute a scheduled SMS. Called by APScheduler at run_date."""
    client = _build_quo_client()
    try:
        result = await execute_sms(
            client, phone, message, from_phone_id, line_name,
            conversation_id="scheduled",
            tool_name="scheduled_sms",
        )
        logfire.info("scheduled SMS executed", phone=phone, result=result)

        if context and "Failed" not in result:
            try:
                await _seed_sms_conversation(phone, message, context)
            except Exception:
                logfire.exception("scheduled_sms: failed to seed conversation", phone=phone)
    finally:
        await client.aclose()


async def _execute_scheduled_email(
    to_str: str,
    subject: str,
    body: str,
    send_as_email: str,
    sender_display: str,
    thread_kwargs: dict | None = None,
) -> None:
    """Execute a scheduled email. Called by APScheduler at run_date."""
    from api.src.google.common.service_account_auth import get_delegated_credentials
    from api.src.google.gmail.service import send_email as gmail_send_email

    credentials = get_delegated_credentials(
        user_email=send_as_email,
        scopes=GMAIL_SCOPES,
    )
    result = await gmail_send_email(
        to=to_str,
        subject=subject,
        message_text=body,
        sender=sender_display,
        credentials=credentials,
        **(thread_kwargs or {}),
    )
    logfire.info("scheduled email sent", to=to_str, result_id=result.get("id"))


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


@scheduling_toolset.tool
async def schedule_sms(
    ctx: RunContext[SerniaDeps],
    to: str,
    message: str,
    send_at: datetime,
    timezone: str = "America/New_York",
    context: str = "",
) -> str:
    """Schedule an SMS for future delivery (one-time, non-recurring).

    Same routing as send_sms: internal contacts use the AI direct line
    (no approval), external contacts use the shared team number
    (requires approval).

    Args:
        to: Recipient phone number in E.164 format (e.g. "+14125551234").
        message: The text message body to send.
        send_at: When to send (e.g. "2026-03-10T14:30:00"). Interpreted
            in the given timezone.
        timezone: IANA timezone for send_at (default "America/New_York").
        context: Optional hidden context (same as send_sms).
    """
    logfire.info("schedule_sms called", to=to, send_at=str(send_at), timezone=timezone)

    # Resolve routing (same logic as send_sms)
    client = _build_quo_client()
    try:
        routing = await resolve_sms_routing(to, client, ctx.deps.conversation_id)
    finally:
        await client.aclose()

    if isinstance(routing, str):
        return routing

    # Same conditional approval as send_sms
    if not routing.is_internal and not ctx.tool_call_approved:
        raise ApprovalRequired()

    # Parse and validate schedule
    aware_dt = _parse_send_at(send_at, timezone)
    if isinstance(aware_dt, str):
        return aware_dt

    # Schedule the job
    job_id = f"{_SMS_JOB_PREFIX}{uuid4().hex[:8]}"
    scheduler = get_scheduler()
    scheduler.add_job(
        func=_execute_scheduled_sms,
        kwargs={
            "phone": to,
            "message": message,
            "from_phone_id": routing.from_phone_id,
            "line_name": routing.line_name,
            "context": context,
        },
        trigger="date",
        run_date=aware_dt,
        id=job_id,
        name=f"SMS to {routing.contact_name} ({to})",
    )

    logfire.info(
        "SMS scheduled",
        job_id=job_id,
        to=to,
        contact_name=routing.contact_name,
        send_at=aware_dt.isoformat(),
    )

    return (
        f"SMS scheduled to {routing.contact_name} ({to}) "
        f"for {aware_dt.strftime('%B %d, %Y at %I:%M %p')} {timezone}. "
        f"Job ID: {job_id}"
    )


@scheduling_toolset.tool
async def schedule_email(
    ctx: RunContext[SerniaDeps],
    to: list[EmailStr],
    subject: str,
    body: str,
    send_at: datetime,
    timezone: str = "America/New_York",
    reply_to_message_id: str = "",
) -> str:
    """Schedule an email for future delivery (one-time, non-recurring).

    Same routing as send_email: all @serniacapital.com recipients use
    your mailbox (no approval), any external recipient uses the shared
    mailbox (requires approval).

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Plain text email body.
        send_at: When to send (e.g. "2026-03-10T09:00:00"). Interpreted
            in the given timezone.
        timezone: IANA timezone for send_at (default "America/New_York").
        reply_to_message_id: Optional Gmail message ID to reply to (threads the email).
    """
    logfire.info("schedule_email called", to=to, subject=subject[:80], send_at=str(send_at))

    if not to:
        return "Blocked: no recipients provided."

    routing = resolve_email_routing(to, ctx.deps.user_email)

    # Same conditional approval as send_email
    if not routing.is_internal and not ctx.tool_call_approved:
        raise ApprovalRequired()

    # Parse and validate schedule
    aware_dt = _parse_send_at(send_at, timezone)
    if isinstance(aware_dt, str):
        return aware_dt

    # Resolve threading at schedule time (headers won't change)
    thread_kwargs: dict | None = None
    if reply_to_message_id:
        if routing.is_internal:
            thread_kwargs = await _get_threading_headers(
                reply_to_message_id, ctx.deps.user_email
            )
        else:
            thread_kwargs = await _get_threading_headers(
                reply_to_message_id, routing.send_as_email
            )
            if not thread_kwargs and ctx.deps.user_email != routing.send_as_email:
                thread_kwargs = await _get_threading_headers(
                    reply_to_message_id, ctx.deps.user_email
                )
                if thread_kwargs:
                    thread_kwargs.pop("thread_id", None)

    # Schedule the job
    to_str = ", ".join(addr.strip() for addr in to)
    job_id = f"{_EMAIL_JOB_PREFIX}{uuid4().hex[:8]}"
    scheduler = get_scheduler()

    job_kwargs: dict = {
        "to_str": to_str,
        "subject": subject,
        "body": body,
        "send_as_email": routing.send_as_email,
        "sender_display": routing.sender_display,
    }
    if thread_kwargs:
        job_kwargs["thread_kwargs"] = thread_kwargs

    scheduler.add_job(
        func=_execute_scheduled_email,
        kwargs=job_kwargs,
        trigger="date",
        run_date=aware_dt,
        id=job_id,
        name=f"Email to {to_str}: {subject[:50]}",
    )

    logfire.info(
        "Email scheduled",
        job_id=job_id,
        to=to_str,
        subject=subject[:80],
        send_at=aware_dt.isoformat(),
    )

    return (
        f"Email scheduled to {to_str} "
        f"for {aware_dt.strftime('%B %d, %Y at %I:%M %p')} {timezone}. "
        f"Subject: {subject}. Job ID: {job_id}"
    )


@scheduling_toolset.tool
async def list_scheduled_messages(
    ctx: RunContext[SerniaDeps],
) -> str:
    """List all pending scheduled SMS and email messages.

    Shows job ID, type, recipient, preview, and scheduled send time.
    Use job IDs with cancel_scheduled_message to cancel.
    """
    scheduler = get_scheduler()
    jobs = [
        j for j in scheduler.get_jobs()
        if j.id.startswith(_SMS_JOB_PREFIX) or j.id.startswith(_EMAIL_JOB_PREFIX)
    ]

    if not jobs:
        return "No scheduled messages pending."

    jobs.sort(key=lambda j: j.next_run_time or datetime.min)

    lines: list[str] = [f"Scheduled messages ({len(jobs)}):"]
    for job in jobs:
        kwargs = job.kwargs or {}
        is_sms = job.id.startswith(_SMS_JOB_PREFIX)
        msg_type = "SMS" if is_sms else "Email"

        if is_sms:
            recipient = kwargs.get("phone", "?")
            preview = (kwargs.get("message") or "")[:80]
            detail = ""
        else:
            recipient = kwargs.get("to_str", "?")
            preview = (kwargs.get("body") or "")[:80]
            detail = f"\n  Subject: {kwargs.get('subject', '?')}"

        send_at_str = (
            job.next_run_time.strftime("%B %d, %Y at %I:%M %p %Z")
            if job.next_run_time else "?"
        )

        lines.append(
            f"\n- [{msg_type}] Job ID: {job.id}"
            f"\n  To: {recipient}{detail}"
            f"\n  Preview: {preview}"
            f"\n  Scheduled: {send_at_str}"
        )

    return "\n".join(lines)


@scheduling_toolset.tool
async def cancel_scheduled_message(
    ctx: RunContext[SerniaDeps],
    job_id: str,
) -> str:
    """Cancel a pending scheduled message by its job ID.

    Use list_scheduled_messages to find job IDs.

    Args:
        job_id: The job ID of the scheduled message to cancel.
    """
    # Safety: only allow canceling scheduled messages, not system jobs
    if not (job_id.startswith(_SMS_JOB_PREFIX) or job_id.startswith(_EMAIL_JOB_PREFIX)):
        return f"Invalid job ID: {job_id}. Only scheduled messages can be canceled."

    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        return f"Job {job_id} not found. It may have already been sent or canceled."

    logfire.info("Scheduled message canceled", job_id=job_id)
    return f"Scheduled message {job_id} canceled successfully."
