"""Durable, per-recipient Twilio dispatch. Acceptance never means human receipt.

The database fences workers, not the provider: after an ambiguous POST we only
reconcile. Studio has no documented create-execution idempotency key.
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx
import logfire
from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.contact.service import get_contact_by_slug
from api.src.database.database import AsyncSessionFactory
from api.src.open_phone.models import EscalationAttempt, EscalationDelivery, EscalationRequest

MAX_SEND_ATTEMPTS = 5
LEASE_SECONDS = 120
EXPIRY_MINUTES = 10
TERMINAL = {"accepted", "failed", "manual", "duplicate"}
SID_PATTERN = re.compile(r"FN[0-9a-fA-F]{32}\Z")


def utcnow() -> datetime:
    return datetime.now(UTC)


def identity(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class DeliveryReport(BaseModel):
    request_id: str
    recipients: dict[str, str]

    @property
    def accepted(self) -> int:
        return sum(state == "accepted" for state in self.recipients.values())


@dataclass
class Outcome:
    state: str
    reason: str | None = None
    sid: str | None = None
    conflict_sid: str | None = None
    retry_after: float = 15
    http_status: int | None = None
    provider_code: int | None = None


async def enqueue_assessment(session: AsyncSession, event: dict) -> str:
    """Caller commits this together with the unique incoming webhook record."""
    message_id = (event.get("event_data") or {}).get("data", {}).get("object", {}).get("id")
    source_key = f"quo-message:{message_id}" if message_id else f"quo-event:{event['event_id']}"
    request_id = identity(source_key)
    now = utcnow()
    # The payload includes datetime values outside event_data.
    payload = json.loads(json.dumps(event, default=lambda v: v.isoformat()))
    await session.execute(
        insert(EscalationRequest)
        .values(
            id=request_id,
            source_key=source_key,
            event_payload=payload,
            state="pending",
            attempts=0,
            created_at=now,
            expires_at=now + timedelta(minutes=EXPIRY_MINUTES),
            next_attempt_at=now,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return request_id


async def add_recipients(
    session: AsyncSession, request_id: str, numbers: list[str] | None = None
) -> None:
    from api.src.open_phone.escalate import DEFAULT_ESCALATION_CONTACT_SLUGS

    # Default contact slugs are durable work items too: a missing lookup must not
    # silently drop one person. Phone numbers freeze on the first successful lookup.
    recipients = (
        [(f"phone:{n}", n) for n in dict.fromkeys(numbers)]
        if numbers
        else [(f"slug:{slug}", None) for slug in DEFAULT_ESCALATION_CONTACT_SLUGS]
    )
    for key, number in recipients:
        await session.execute(
            insert(EscalationDelivery)
            .values(
                id=identity(f"{request_id}:{key}"),
                request_id=request_id,
                recipient_key=key,
                recipient=number,
                state="pending",
                attempts=0,
                next_attempt_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )


async def dispatch(
    message: str, *, source_key: str, numbers: list[str] | None = None
) -> DeliveryReport:
    if not source_key:
        raise ValueError("Escalation requires a stable source key")
    request_id, now = identity(source_key), utcnow()
    async with AsyncSessionFactory() as session, session.begin():
        created = (
            await session.execute(
                insert(EscalationRequest)
                .values(
                    id=request_id,
                    source_key=source_key,
                    message_text=message,
                    state="ready",
                    attempts=0,
                    created_at=now,
                    expires_at=now + timedelta(minutes=EXPIRY_MINUTES),
                    next_attempt_at=now,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(EscalationRequest.id)
            )
        ).scalar_one_or_none()
        if created:
            await add_recipients(session, request_id, numbers)
    await process_deliveries(request_id)
    return await report(request_id)


async def report(request_id: str) -> DeliveryReport:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.scalars(
                select(EscalationDelivery).where(EscalationDelivery.request_id == request_id)
            )
        ).all()
    return DeliveryReport(
        request_id=request_id, recipients={r.recipient_key: r.state for r in rows}
    )


async def process_request(request_id: str) -> None:
    """Recoverable assessment; no provider side effect until the decision is committed."""
    from api.src.open_phone.escalate import ai_assess_for_escalation, never_escalate_from_numbers

    token, now = str(uuid4()), utcnow()
    async with AsyncSessionFactory() as session, session.begin():
        row = await session.scalar(
            select(EscalationRequest).where(EscalationRequest.id == request_id).with_for_update()
        )
        if not row or row.state not in {"pending", "assessing"} or row.next_attempt_at > now:
            return
        if row.lease_until and row.lease_until > now:
            return
        if row.expires_at <= now or row.attempts >= 3:
            row.state = "manual"
            row.lease_token = row.lease_until = None
            logfire.error("Escalation assessment requires attention", request_id=request_id)
            return
        row.state, row.lease_token = "assessing", token
        row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        row.attempts += 1
        event = row.event_payload
    try:
        if event.get("from_number") in never_escalate_from_numbers:
            should_escalate = False
        else:
            should_escalate, _ = await ai_assess_for_escalation(event)
        async with AsyncSessionFactory() as session, session.begin():
            row = await session.scalar(
                select(EscalationRequest)
                .where(EscalationRequest.id == request_id, EscalationRequest.lease_token == token)
                .with_for_update()
            )
            if not row:
                return
            row.state = "ready" if should_escalate else "skipped"
            row.lease_token = row.lease_until = None
            if should_escalate:
                row.message_text = (
                    f"URGENT! {event.get('from_number')} said: {event.get('message_text')}"
                )
                await add_recipients(session, request_id)
        if should_escalate:
            await process_deliveries(request_id)
    except Exception as exc:
        async with AsyncSessionFactory() as session, session.begin():
            await session.execute(
                update(EscalationRequest)
                .where(EscalationRequest.id == request_id, EscalationRequest.lease_token == token)
                .values(
                    state="pending",
                    lease_token=None,
                    lease_until=None,
                    next_attempt_at=utcnow() + timedelta(seconds=30),
                    last_error=type(exc).__name__,
                )
            )
        logfire.warn(
            "Escalation assessment will retry", request_id=request_id, error_type=type(exc).__name__
        )


class StudioClient:
    def __init__(self, client: httpx.AsyncClient, flow: str, from_number: str):
        self.client, self.flow, self.from_number = client, flow, from_number
        self.base = f"https://studio.twilio.com/v2/Flows/{flow}/Executions"

    async def execution(self, sid: str) -> dict:
        if not SID_PATTERN.fullmatch(sid):
            raise ValueError("Invalid execution SID")
        response = await self.client.get(f"{self.base}/{sid}")
        response.raise_for_status()
        return response.json()

    async def matches(self, sid: str, delivery: EscalationDelivery) -> bool:
        response = await self.client.get(f"{self.base}/{sid}/Context")
        response.raise_for_status()
        ctx = response.json().get("context", {})
        flow = ctx.get("flow", {})
        return (
            ctx.get("contact", {}).get("channel", {}).get("address") == delivery.recipient
            and flow.get("channel", {}).get("address") == self.from_number
            and flow.get("flow_sid") == self.flow
            and flow.get("data", {}).get("escalation_delivery_id") == delivery.id
        )

    async def check_conflict(self, delivery: EscalationDelivery, sid: str) -> Outcome:
        execution = await self.execution(sid)
        if (
            execution.get("contact_channel_address") != delivery.recipient
            or execution.get("flow_sid") != self.flow
        ):
            return Outcome("unknown", "conflict_identity_mismatch")
        if await self.matches(sid, delivery):
            return Outcome("accepted", sid=sid)
        if execution.get("status") == "ended":
            return Outcome("retry", "previous_execution_ended")
        if execution.get("status") == "active":
            return Outcome("blocked", "recipient_busy", conflict_sid=sid)
        return Outcome("unknown", "unexpected_execution_status")

    async def reconcile(self, delivery: EscalationDelivery, request: EscalationRequest) -> Outcome:
        # A bounded listing can prove a match, never prove absence. Do not POST
        # after a negative search, including when the prior execution has ended.
        response = await self.client.get(
            self.base,
            params={
                "DateCreatedFrom": (request.created_at - timedelta(minutes=2))
                .replace(microsecond=0)
                .isoformat(),
                "PageSize": 100,
            },
        )
        response.raise_for_status()
        for execution in response.json().get("executions", []):
            sid = execution.get("sid", "")
            if (
                execution.get("contact_channel_address") == delivery.recipient
                and SID_PATTERN.fullmatch(sid)
                and await self.matches(sid, delivery)
            ):
                return Outcome("accepted", sid=sid)
        return Outcome("unknown", "acceptance_not_verified", retry_after=30)

    async def send(self, delivery: EscalationDelivery, request: EscalationRequest) -> Outcome:
        try:
            response = await self.client.post(
                self.base,
                data={
                    "To": delivery.recipient,
                    "From": self.from_number,
                    "Parameters": json.dumps(
                        {
                            "message_text": f"{request.message_text}\nIncident ID: {request.id[:12]}",
                            "escalation_delivery_id": delivery.id,
                        }
                    ),
                },
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            return Outcome("retry", type(exc).__name__)
        except httpx.HTTPError as exc:
            return Outcome("unknown", type(exc).__name__)
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        status, code = response.status_code, data.get("code")
        code = code if isinstance(code, int) else None
        if status == 201 and SID_PATTERN.fullmatch(data.get("sid", "")):
            return Outcome("accepted", sid=data["sid"], http_status=status)
        if status == 409 and code == 20409:
            sid = data.get("details", {}).get("conflicting_execution_sid", "")
            if SID_PATTERN.fullmatch(sid):
                # Persist the known rejection before reconciliation; failure of
                # a GET must not turn this rejected POST into a resend.
                return Outcome(
                    "blocked",
                    "recipient_busy",
                    conflict_sid=sid,
                    http_status=status,
                    provider_code=code,
                )
        if status == 429:
            try:
                delay = max(15, min(float(response.headers.get("Retry-After", "15")), 600))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(response.headers["Retry-After"])
                    delay = max(15, (retry_at - utcnow()).total_seconds())
                except (ValueError, KeyError, TypeError):
                    delay = 30
            return Outcome(
                "retry", "rate_limited", retry_after=delay, http_status=status, provider_code=code
            )
        if 400 <= status < 500 and status != 408:
            return Outcome("failed", "provider_rejected", http_status=status, provider_code=code)
        return Outcome(
            "unknown", "ambiguous_provider_response", http_status=status, provider_code=code
        )


async def process_delivery(delivery_id: str, studio: StudioClient) -> None:
    token, now = str(uuid4()), utcnow()
    async with AsyncSessionFactory() as session, session.begin():
        row = await session.scalar(
            select(EscalationDelivery).where(EscalationDelivery.id == delivery_id).with_for_update()
        )
        if not row or row.state in TERMINAL or row.next_attempt_at > now:
            return
        if row.lease_until and row.lease_until > now:
            return
        request = await session.get(EscalationRequest, row.request_id)
        # A crashed/stalled sender may have reached Twilio. Never reclaim as pending.
        if row.state == "sending":
            await session.execute(
                update(EscalationAttempt)
                .where(
                    EscalationAttempt.id == row.lease_token, EscalationAttempt.outcome == "sending"
                )
                .values(outcome="unknown", finished_at=now)
            )
            row.state, row.last_error = "unknown", "interrupted_send"
        if request.expires_at <= now:
            row.state, row.last_error = "manual", "delivery_deadline_exceeded"
            row.lease_token = row.lease_until = None
            logfire.error(
                "Escalation delivery requires attention",
                request_id=row.request_id,
                delivery_id=row.id,
                recipient_suffix=(row.recipient or "")[-4:],
                reason=row.last_error,
            )
            return
        row.lease_token, row.lease_until = token, now + timedelta(seconds=LEASE_SECONDS)

    outcome = None
    try:
        async with asyncio.timeout(45):
            if not row.recipient:
                contact = await get_contact_by_slug(row.recipient_key.removeprefix("slug:"))
                number = contact.phone_number if contact else None
                if not number:
                    outcome = Outcome("retry", "recipient_unavailable", retry_after=30)
                else:
                    async with AsyncSessionFactory() as session, session.begin():
                        # Serialize number freezing within this request so two contact
                        # slugs resolving to the same phone cannot both send.
                        await session.scalar(
                            select(EscalationRequest)
                            .where(EscalationRequest.id == row.request_id)
                            .with_for_update()
                        )
                        duplicate = await session.scalar(
                            select(EscalationDelivery.id)
                            .where(
                                EscalationDelivery.request_id == row.request_id,
                                EscalationDelivery.recipient == number,
                                EscalationDelivery.id != row.id,
                            )
                            .limit(1)
                        )
                        if duplicate:
                            outcome = Outcome("duplicate", "recipient_already_queued")
                        frozen = (
                            await session.execute(
                                update(EscalationDelivery)
                                .where(
                                    EscalationDelivery.id == row.id,
                                    EscalationDelivery.lease_token == token,
                                )
                                .values(recipient=None if duplicate else number)
                            )
                        ).rowcount
                    if not frozen:
                        return
                    row.recipient = number
            if outcome is None and row.state == "unknown":
                outcome = await studio.reconcile(row, request)
            elif outcome is None and row.conflict_sid:
                outcome = await studio.check_conflict(row, row.conflict_sid)
            if outcome is None:
                # Avoid known active-flow conflicts for a later distinct source.
                async with AsyncSessionFactory() as session:
                    previous = await session.scalar(
                        select(EscalationDelivery)
                        .join(
                            EscalationRequest, EscalationDelivery.request_id == EscalationRequest.id
                        )
                        .where(
                            EscalationRequest.created_at
                            > utcnow() - timedelta(minutes=EXPIRY_MINUTES),
                            EscalationDelivery.recipient == row.recipient,
                            EscalationDelivery.execution_sid.is_not(None),
                            EscalationDelivery.id != row.id,
                        )
                        .order_by(EscalationDelivery.next_attempt_at.desc())
                        .limit(1)
                    )
                if previous:
                    check = await studio.check_conflict(row, previous.execution_sid)
                    if check.state != "retry":
                        outcome = check
            if outcome is None:
                if row.attempts >= MAX_SEND_ATTEMPTS:
                    outcome = Outcome("manual", "retry_limit_exceeded")
                else:
                    # Fence again immediately before the irreversible boundary:
                    # contact lookup / preflight may have outlived this lease.
                    async with AsyncSessionFactory() as session, session.begin():
                        claimed = await session.scalar(
                            select(EscalationDelivery)
                            .where(
                                EscalationDelivery.id == row.id,
                                EscalationDelivery.lease_token == token,
                                EscalationDelivery.lease_until > utcnow(),
                                EscalationDelivery.state.in_(["pending", "retry"]),
                            )
                            .with_for_update()
                        )
                        if not claimed:
                            return
                        current_request = await session.get(EscalationRequest, row.request_id)
                        if current_request.expires_at <= utcnow():
                            outcome = Outcome("manual", "delivery_deadline_exceeded")
                        else:
                            claimed.state = "sending"
                            claimed.attempts += 1
                            session.add(
                                EscalationAttempt(
                                    id=token,
                                    delivery_id=row.id,
                                    started_at=utcnow(),
                                    outcome="sending",
                                )
                            )
                    if outcome is None:
                        row.attempts += 1
                        row.state = "sending"
                        outcome = await studio.send(row, request)

    except Exception as exc:
        # Only pre-send/read failures are safe to retry. Once sending or unknown,
        # an unexpected exception must preserve uncertainty.
        state = (
            "unknown"
            if row.state in {"sending", "unknown"}
            else "blocked"
            if row.conflict_sid
            else "retry"
        )
        outcome = Outcome(state, type(exc).__name__, conflict_sid=row.conflict_sid, retry_after=30)

    async with AsyncSessionFactory() as session, session.begin():
        owned = await session.scalar(
            select(EscalationDelivery)
            .where(EscalationDelivery.id == row.id, EscalationDelivery.lease_token == token)
            .with_for_update()
        )
        if not owned:
            return
        owned.state, owned.last_error = outcome.state, outcome.reason
        owned.execution_sid, owned.conflict_sid = outcome.sid, outcome.conflict_sid
        owned.lease_token = owned.lease_until = None
        delay = max(outcome.retry_after, min(120, 15 * 2 ** max(0, row.attempts - 1)))
        # Wake at expiry even when Retry-After extends beyond the lifetime.
        # This wake marks manual; it never sends before the provider retry time.
        owned.next_attempt_at = min(utcnow() + timedelta(seconds=delay), request.expires_at)
        await session.execute(
            update(EscalationAttempt)
            .where(EscalationAttempt.id == token)
            .values(
                finished_at=utcnow(),
                outcome=outcome.state,
                http_status=outcome.http_status,
                provider_code=outcome.provider_code,
                execution_sid=outcome.sid or outcome.conflict_sid,
            )
        )
    logger = (
        logfire.error
        if outcome.state in {"failed", "manual"}
        else logfire.warn
        if outcome.state == "unknown"
        else logfire.info
    )
    logger(
        "Escalation delivery outcome",
        request_id=row.request_id,
        delivery_id=row.id,
        recipient_suffix=(row.recipient or "")[-4:],
        state=outcome.state,
        reason=outcome.reason,
        execution_sid=outcome.sid,
        conflict_sid=outcome.conflict_sid,
    )


async def process_deliveries(request_id: str | None = None) -> None:
    from api.src.open_phone.escalate import (
        ESCALATE_FROM_NUMBER,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FLOW_ID,
    )

    async with AsyncSessionFactory() as session:
        query = (
            select(EscalationDelivery.id)
            .where(
                EscalationDelivery.state.not_in(TERMINAL),
                EscalationDelivery.next_attempt_at <= utcnow(),
                or_(
                    EscalationDelivery.lease_until.is_(None),
                    EscalationDelivery.lease_until <= utcnow(),
                ),
            )
            .order_by(EscalationDelivery.next_attempt_at)
            .limit(20)
        )
        if request_id:
            query = query.where(EscalationDelivery.request_id == request_id)
        ids = (await session.scalars(query)).all()
    async with httpx.AsyncClient(
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=20
    ) as client:
        studio = StudioClient(client, TWILIO_FLOW_ID, ESCALATE_FROM_NUMBER)
        await asyncio.gather(*(process_delivery(i, studio) for i in ids))


async def recover_escalations() -> None:
    async with AsyncSessionFactory() as session:
        ids = (
            await session.scalars(
                select(EscalationRequest.id)
                .where(
                    EscalationRequest.state.in_(["pending", "assessing"]),
                    EscalationRequest.next_attempt_at <= utcnow(),
                    or_(
                        EscalationRequest.lease_until.is_(None),
                        EscalationRequest.lease_until <= utcnow(),
                    ),
                )
                .order_by(EscalationRequest.next_attempt_at)
                .limit(10)
            )
        ).all()
    await asyncio.gather(process_deliveries(), *(process_request(i) for i in ids))


def register_escalation_recovery() -> None:
    from api.src.apscheduler_service.service import get_scheduler, upsert_job

    upsert_job(
        get_scheduler(),
        id="escalation-outbox",
        func=recover_escalations,
        trigger="interval",
        seconds=15,
        max_instances=1,
        coalesce=True,
    )
