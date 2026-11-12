"""A Zillow lead's email thread, read from the shared mailbox only.

The Gmail watch runs on one person's mailbox, but replies go out from the
shared mailbox, and Gmail message and thread IDs are different in every
mailbox. Everything downstream of the queue therefore works in the shared
mailbox, and this module is the one place that crosses over: the RFC
``Message-ID`` header is the same on every copy of an email, so one search by
that header finds the shared mailbox's copy.

Loading is deterministic (no model calls, no truncation) so a thread loaded
today can be stored and replayed later with the same content.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from googleapiclient.errors import HttpError
from pydantic import BaseModel

from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.service import extract_email_body, get_gmail_service
from api.src.sernia_ai.config import (
    GOOGLE_DELEGATION_EMAIL,
    INTERNAL_EMAIL_DOMAIN,
    SHARED_EXTERNAL_EMAIL,
)
from api.src.sernia_ai.tools.google_tools import (
    _clean_zillow_email,
    _html_to_markdown,
    _is_zillow_content,
    _strip_quoted_replies,
)

_READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass(frozen=True)
class SharedCopy:
    """Where an email lives in the shared mailbox."""

    message_id: str
    thread_id: str
    rfc_message_id: str


class LeadMessage(BaseModel):
    """One email in a lead thread, with boilerplate and quoted replies removed."""

    message_id: str  # Gmail ID in the shared mailbox; pass to send_email to reply
    rfc_message_id: str
    sent_at: datetime
    from_address: str
    reply_to: str | None
    from_sernia: bool
    subject: str
    body: str


class LeadThread(BaseModel):
    """A lead conversation as the shared mailbox holds it, oldest message first."""

    mailbox: str
    thread_id: str
    messages: list[LeadMessage]


def _gmail(mailbox: str) -> Any:
    return get_gmail_service(get_delegated_credentials(user_email=mailbox, scopes=_READONLY))


def _headers(message: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def _rfc_message_id(mailbox: str, message_id: str) -> str | None:
    """The email's RFC Message-ID header, or None if the mailbox has no such email."""
    try:
        message = (
            _gmail(mailbox)
            .users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=["Message-ID"])
            .execute()
        )
    except HttpError as error:
        if error.resp.status == 404:
            return None
        raise
    return _headers(message).get("message-id") or None


def _search_by_rfc_id(mailbox: str, rfc_message_id: str) -> dict[str, str] | None:
    matches = (
        _gmail(mailbox)
        .users()
        .messages()
        .list(userId="me", q=f"rfc822msgid:{rfc_message_id}", maxResults=1)
        .execute()
        .get("messages", [])
    )
    return matches[0] if matches else None


async def find_shared_copy(
    message_id: str, *, source_mailbox: str = GOOGLE_DELEGATION_EMAIL
) -> SharedCopy | None:
    """Find the shared mailbox's copy of an email seen in ``source_mailbox``.

    Returns None when the email is gone from the source mailbox or has no copy
    in the shared one. Gmail errors other than "not found" propagate, so a
    caller with retries (the Zillow queue) retries them.
    """
    rfc_message_id = await asyncio.to_thread(_rfc_message_id, source_mailbox, message_id)
    if not rfc_message_id:
        return None
    match = await asyncio.to_thread(_search_by_rfc_id, SHARED_EXTERNAL_EMAIL, rfc_message_id)
    if match is None:
        return None
    return SharedCopy(
        message_id=match["id"], thread_id=match["threadId"], rfc_message_id=rfc_message_id
    )


# Zillow relays a lead's message inside its own form text. Two shapes carry it:
#   a later message: "<message> Regarding your listing at: <address> <suggested
#       replies> <scam warning> RENTER'S NAME <name> RENTER'S MESSAGE <message>"
#       The address ends at its ZIP code; the suggested replies are dropped.
#   a first inquiry: "<message> Send application About <name> Move in <date>
#       Credit score <range> Income ... Number of Occupants <n>". Any field may
#       be missing, "Send application" too.
_RELAY = re.compile(
    r"Regarding your listing at:\s*(?P<address>.*?\b\d{5}\b).*?Some rental inquiries may be scams\."
    r".*?RENTER'S NAME\s+(?P<name>.*?)\s+RENTER'S MESSAGE\s*(?P<message>.*)\Z",
    re.S,
)
# The same form with its tail cut off, which leaves only the first copy of the message.
_RELAY_HEAD = re.compile(
    r"\s*Regarding your listing at:\s*(?P<address>.*?\b\d{5}\b).*?Some rental inquiries may be scams\..*\Z",
    re.S,
)
_PROFILE_FIELDS = (
    "Move in",
    "Credit score",
    "Income",
    "Pets",
    "Lease Length",
    "Number of Bedrooms",
    "Number of Occupants",
)
_PROFILE_FIELD = re.compile("(" + "|".join(map(re.escape, _PROFILE_FIELDS)) + r")\s+")
# The profile starts at "About <name>" followed by any field label. A lead who
# gave no move-in date has no "Move in" field, so the first label varies. The
# name is short and on one line, which keeps a sentence that merely contains
# "about" from matching.
_PROFILE = re.compile(
    r"\s*(?:Send application\s*)?About\s+(?P<name>\S[^\n]{0,60}?)\s{2,}(?="
    + _PROFILE_FIELD.pattern
    + ")"
)
_PADDING = " \xa0\n"  # Zillow separates fields with non-breaking spaces
# The button ends the lead's message. Zillow sometimes follows it with suggested
# replies and an advert for its own application, which go with it.
_BUTTON = re.compile(r"\s*Send application.*\Z", re.S)


def tidy_zillow_form(body: str) -> str:
    """A lead's message with Zillow's form text removed or labelled.

    The lead's own words come first, once. What Zillow added that is worth
    keeping (the listing, the lead's name, the profile they filled in) follows
    under a heading that says where it came from, so it is not mistaken for
    something the lead wrote to us. Button text such as "Send application" is
    dropped. Safe to apply twice.
    """
    details: list[str] = []
    relay = _RELAY.search(body)
    if relay:
        # The complete copy follows RENTER'S MESSAGE; fall back to the first copy.
        body = relay["message"].strip() or body[: relay.start()]
        details += [f"- Listing: {relay['address']}", f"- Name: {relay['name'].strip()}"]
    elif head := _RELAY_HEAD.search(body):
        body = body[: head.start()]
        details.append(f"- Listing: {head['address']}")
    profile = _PROFILE.search(body)
    if profile:
        parts = _PROFILE_FIELD.split(body[profile.end() :])
        body = body[: profile.start()]
        details.append(f"- Name: {profile['name'].strip()}")
        details += [
            f"- {label}: {value.strip(_PADDING)}"
            for label, value in zip(parts[1::2], parts[2::2], strict=True)
        ]
    body = _BUTTON.sub("", body).strip()
    if not details:
        return body
    # Zillow sometimes relays the form with no message in it at all.
    return "\n".join(
        [
            body or "(no message text)",
            "",
            "Zillow form details (not written by the lead):",
            *details,
        ]
    )


def _to_lead_message(message: dict[str, Any]) -> LeadMessage:
    headers = _headers(message)
    from_header = headers.get("from", "")
    from_address = parseaddr(from_header)[1].lower()
    reply_to = parseaddr(headers.get("reply-to", ""))[1].lower() or None

    parts = extract_email_body(message)
    body = parts.get("text") or ""
    if not body and parts.get("html"):
        body = _html_to_markdown(parts["html"])
    if _is_zillow_content(from_header, body):
        body = _clean_zillow_email(body)
    body = _strip_quoted_replies(body).strip()
    if not from_address.endswith(f"@{INTERNAL_EMAIL_DOMAIN}"):
        body = tidy_zillow_form(body)

    return LeadMessage(
        message_id=message["id"],
        rfc_message_id=headers.get("message-id", ""),
        # Gmail's own receive time: always present and timezone-safe, unlike
        # the sender-written Date header.
        sent_at=datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=UTC),
        from_address=from_address,
        reply_to=reply_to,
        from_sernia=from_address.endswith(f"@{INTERNAL_EMAIL_DOMAIN}"),
        subject=headers.get("subject", ""),
        body=body,
    )


def _get_thread(mailbox: str, thread_id: str) -> dict[str, Any] | None:
    try:
        return (
            _gmail(mailbox)
            .users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except HttpError as error:
        if error.resp.status == 404:
            return None
        raise


async def load_lead_thread(thread_id: str) -> LeadThread | None:
    """Load a thread from the shared mailbox by its shared-mailbox thread ID.

    Returns None when the thread does not exist there.
    """
    thread = await asyncio.to_thread(_get_thread, SHARED_EXTERNAL_EMAIL, thread_id)
    if thread is None:
        return None
    messages = sorted(
        (_to_lead_message(m) for m in thread.get("messages", [])), key=lambda m: m.sent_at
    )
    return LeadThread(mailbox=SHARED_EXTERNAL_EMAIL, thread_id=thread_id, messages=messages)


def _list_thread_ids(mailbox: str, query: str) -> list[str]:
    service = _gmail(mailbox)
    thread_ids: list[str] = []
    page_token: str | None = None
    while True:
        page = (
            service.users()
            .threads()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute()
        )
        thread_ids += [thread["id"] for thread in page.get("threads", [])]
        page_token = page.get("nextPageToken")
        if not page_token:
            return thread_ids


async def find_lead_thread_ids(query: str) -> list[str]:
    """Shared-mailbox thread IDs matching a Gmail search, for exporting past threads."""
    return await asyncio.to_thread(_list_thread_ids, SHARED_EXTERNAL_EMAIL, query)
