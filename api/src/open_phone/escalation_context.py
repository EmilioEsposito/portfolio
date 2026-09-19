"""Bounded, best-effort Quo context for an incoming escalation decision."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import logfire
from pydantic import BaseModel, Field

HISTORY_DAYS = 3
HISTORY_MESSAGES = 20
HISTORY_TIMEOUT = 5.0
EASTERN = ZoneInfo("America/New_York")


class HistoryMessage(BaseModel):
    timestamp: str
    direction: str
    text: str
    same_sender: bool


class EscalationHistory(BaseModel):
    status: str
    source: str = "quo_api"
    messages: list[HistoryMessage] = Field(default_factory=list)


def event_time(value: datetime | str) -> datetime:
    """Preserve legacy naive-as-Eastern behavior; represent aware times in Eastern."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    return dt.astimezone(EASTERN)


def add_history_timing(state: dict) -> dict:
    """Add event-relative facts, not a model-derived acknowledgement or decision.

    UTC subtraction is required across DST transitions. Last-message fields refer
    only to supplied history, which can be partial, not the entire conversation.
    """
    current = event_time(state["timestamp"]).astimezone(UTC)
    messages = []
    for original in state.get("prior_messages", []):
        seconds = (current - event_time(original["timestamp"]).astimezone(UTC)).total_seconds()
        if seconds < 0:
            raise ValueError("History must not contain future messages")
        messages.append(
            {
                **original,
                "seconds_before_current_message": seconds,
                "minutes_before_current_message": seconds / 60,
                "within_previous_30_minutes": seconds <= 1800,
            }
        )
    ages = [m["seconds_before_current_message"] for m in messages]
    outbound_ages = [
        m["seconds_before_current_message"] for m in messages if m["direction"] == "outgoing"
    ]
    return {
        **state,
        "prior_messages": messages,
        "history_timing": {
            "reference": "current_message_timestamp",
            "seconds_since_last_message_in_supplied_history": min(ages) if ages else None,
            "seconds_since_last_outbound_in_supplied_history": min(outbound_ages)
            if outbound_ages
            else None,
            "outbound_message_within_previous_30_minutes": any(
                age <= 1800 for age in outbound_ages
            ),
        },
    }


async def fetch_escalation_history(event: dict) -> EscalationHistory:
    """Never query the events DB or block assessment indefinitely on a history failure.

    Retrieve at most 100 messages and retain the newest 20 text messages in the
    preceding 72 hours. Validate thread identity and timestamps locally as well
    as server-side; historical replays must not see later replies or edited text.
    """
    phone_id = event.get("phone_number_id")
    conversation_id = event.get("conversation_id")
    sender = event.get("from_number")
    if not all((phone_id, conversation_id, sender)):
        return EscalationHistory(status="missing_thread_identity")
    key = os.getenv("OPEN_PHONE_API_KEY")
    if not key:
        return EscalationHistory(status="missing_api_key")
    try:
        obj = (event.get("event_data") or {}).get("data", {}).get("object", {})
        cutoff = event_time(obj.get("createdAt") or event["event_timestamp"])
        after = cutoff - timedelta(days=HISTORY_DAYS)
        async with asyncio.timeout(HISTORY_TIMEOUT):
            async with httpx.AsyncClient(
                base_url="https://api.openphone.com",
                headers={"Authorization": key},
                timeout=HISTORY_TIMEOUT,
            ) as client:
                inbox = await client.get(f"/v1/phone-numbers/{phone_id}")
                inbox.raise_for_status()
                own_number = inbox.json()["data"]["number"]
                recipients = event.get("to_number") or []
                if isinstance(recipients, str):
                    recipients = recipients.split(",")
                participants = sorted({sender, *recipients} - {own_number, ""})
                if own_number not in recipients or not participants:
                    return EscalationHistory(status="unverified_participants")
                response = await client.get(
                    "/v1/messages",
                    params=[
                        ("phoneNumberId", phone_id),
                        ("maxResults", "100"),
                        ("createdAfter", after.astimezone(UTC).isoformat()),
                        ("createdBefore", cutoff.astimezone(UTC).isoformat()),
                        *(("participants", p) for p in participants),
                    ],
                )
                response.raise_for_status()
                payload = response.json()
        raw = payload["data"]
        if any(m.get("conversationId") != conversation_id for m in raw):
            return EscalationHistory(status="thread_mismatch")
        retained = []
        seen = set()
        omitted = bool(payload.get("nextPageToken"))
        for message in raw:
            message_id = message.get("id")
            if not message_id or message_id in seen or message_id == obj.get("id"):
                continue
            seen.add(message_id)
            timestamp = event_time(message["createdAt"])
            if not after <= timestamp < cutoff:
                continue
            if message.get("updatedAt") and event_time(message["updatedAt"]) > cutoff:
                omitted = True
                continue
            text = message.get("text") or message.get("body") or ""
            if not text.strip():
                continue  # no images or invented descriptions of attachments
            retained.append(
                HistoryMessage(
                    timestamp=timestamp.isoformat(),
                    direction=message["direction"],
                    text=text,
                    same_sender=message.get("from") == sender,
                )
            )
        retained.sort(key=lambda m: datetime.fromisoformat(m.timestamp))
        omitted |= len(retained) > HISTORY_MESSAGES
        retained = retained[-HISTORY_MESSAGES:]
        # Bound payload size without truncating a message into misleading prose.
        while sum(len(m.text) for m in retained) > 16000:
            retained.pop(0)
            omitted = True
        return EscalationHistory(status="partial" if omitted else "available", messages=retained)
    except Exception as exc:
        logfire.warn("Escalation history unavailable", error_type=type(exc).__name__)
        return EscalationHistory(status="unavailable")
