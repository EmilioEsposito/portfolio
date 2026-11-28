"""The prospect's emails about a showing, laid out, and the calendar file they link to.

The words are ``wording.py``'s (``ProspectEmail``, ``PROSPECT_EMAIL_VERSION``);
this module only lays them out:

- ``payload``: an ``EmailPayload`` with the subject, a plain-text body and an
  HTML body in the Sernia public pages' colours. It is a plain email from the
  shared mailbox, sent through the outbox: no calendar invitation and no
  attachment, so no mail client treats it as an invitation from an unknown
  sender.
- ``google_calendar_url``: Google Calendar's "add event" page, filled in.
- ``map_url``: the address on Google Maps.
- ``ics``: the showing as an iCalendar file (``METHOD:PUBLISH``, no organizer,
  no attendees) for Apple and Outlook calendars. The web app serves it at the
  email's calendar link, keyed by the cancel token
  (``POST /public/bookings/calendar``), and it holds no more than the cancel
  page shows: the address, the times and the cancel link.

Nothing here reads the database or sends anything.
"""

import hashlib
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode

from api.src.showing_bookings import wording
from api.src.showing_bookings.schema import EmailPayload

FONT = "Figtree,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK = "#2e3033"
MUTED = "#5e656e"
LINE = "#dae1e7"
SKY = "#5bbdee"
BAND = "#eaf5fc"
PAGE = "#f4f6f8"
# The event's own id on the prospect's calendar, so adding it twice updates one event.
UID_DOMAIN = "showings.serniacapital.com"


def map_url(address: str) -> str:
    return "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": address})


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def google_calendar_url(visit: wording.Visit, cancel_url: str) -> str:
    """Google Calendar's page to add the showing, with its title, times, place and
    the cancel link filled in. Nothing is added until the person saves it."""
    return "https://calendar.google.com/calendar/render?" + urlencode(
        {
            "action": "TEMPLATE",
            "text": wording.showing_event_title(visit),
            "dates": f"{_stamp(visit.starts_at)}/{_stamp(visit.ends_at)}",
            "details": wording.showing_event_details(cancel_url),
            "location": visit.address or f"unit {visit.unit}",
        }
    )


def calendar_links(
    visit: wording.Visit, *, cancel_url: str, file_url: str
) -> list[wording.EmailLink]:
    return [
        wording.EmailLink(wording.GOOGLE_CALENDAR_LABEL, google_calendar_url(visit, cancel_url)),
        wording.EmailLink(wording.OTHER_CALENDAR_LABEL, file_url),
    ]


def confirmation(
    visit: wording.Visit,
    *,
    cancel_url: str,
    file_url: str,
    replaces: "list[datetime] | tuple[datetime, ...]" = (),
) -> EmailPayload:
    """The confirmation email, worded as a move with ``replaces``, laid out."""
    return payload(
        wording.confirmation_email(
            visit,
            cancel_url=cancel_url,
            map_url=map_url(visit.address) if visit.address else None,
            calendar=calendar_links(visit, cancel_url=cancel_url, file_url=file_url),
            replaces=replaces,
        )
    )


# ---------------------------------------------------------------------------
# Plain text and HTML
# ---------------------------------------------------------------------------


def text(email: wording.ProspectEmail) -> str:
    parts = [f"{email.heading}."]
    parts += list(email.lines)
    if email.when or email.where:
        details = []
        if email.when:
            details.append(f"When: {email.when}")
        if email.where:
            details.append(f"Where: {email.where}")
        if email.map_url:
            details.append(f"Map: {email.map_url}")
        parts.append("\n".join(details))
    if email.note:
        parts.append(email.note)
    if email.calendar:
        parts.append("\n".join(f"{link.label}: {link.url}" for link in email.calendar))
    if email.closing:
        parts.append(f"{email.closing.label}: {email.closing.url}")
    parts.append(wording.EMAIL_SIGN_OFF)
    return "\n\n".join(parts)


def _p(content: str, *, size: int = 16, color: str = INK, top: int = 12) -> str:
    return (
        f'<tr><td style="padding:{top}px 28px 0;font-family:{FONT};font-size:{size}px;'
        f'line-height:1.55;color:{color};">{content}</td></tr>'
    )


def _link(link: wording.EmailLink) -> str:
    return (
        f'<a href="{escape(link.url)}" style="color:{INK};text-decoration:underline;">'
        f"{escape(link.label)}</a>"
    )


def _button(link: wording.EmailLink, *, primary: bool) -> str:
    colours = (
        f"background:{INK};color:#ffffff;border:1px solid {INK};"
        if primary
        else f"background:#ffffff;color:{INK};border:1px solid {LINE};"
    )
    return (
        '<tr><td style="padding:12px 28px 0;">'
        f'<a href="{escape(link.url)}" style="display:block;{colours}border-radius:8px;'
        f"padding:13px 16px;font-family:{FONT};font-size:16px;font-weight:600;"
        f'line-height:1.25;text-align:center;text-decoration:none;">{escape(link.label)}</a>'
        "</td></tr>"
    )


def _details(email: wording.ProspectEmail) -> str:
    rows = []
    if email.when:
        rows.append(("When", escape(email.when)))
    if email.where:
        where = escape(email.where)
        if email.map_url:
            where += (
                f'<br><a href="{escape(email.map_url)}" style="color:{INK};'
                'text-decoration:underline;">Open in Maps</a>'
            )
        rows.append(("Where", where))
    cells = "".join(
        f'<tr><td style="padding:{"0" if index == 0 else "12px"} 0 0;font-family:{FONT};">'
        f'<div style="font-size:13px;line-height:1.4;font-weight:600;letter-spacing:0.04em;'
        f'text-transform:uppercase;color:{MUTED};">{label}</div>'
        f'<div style="font-size:16px;line-height:1.5;color:{INK};">{value}</div></td></tr>'
        for index, (label, value) in enumerate(rows)
    )
    return (
        '<tr><td style="padding:20px 28px 0;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{BAND};border-radius:8px;"><tr><td style="padding:16px 18px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"{cells}</table></td></tr></table></td></tr>"
    )


def html(email: wording.ProspectEmail) -> str:
    """The email as HTML: one card, tables and inline styles only, as mail clients need."""
    rows = [
        f'<tr><td style="height:4px;line-height:4px;font-size:0;background:{SKY};'
        'border-radius:12px 12px 0 0;">&nbsp;</td></tr>',
        f'<tr><td style="padding:24px 28px 0;font-family:{FONT};font-size:13px;'
        f'line-height:1.4;font-weight:600;letter-spacing:0.08em;color:{MUTED};">'
        f"{escape(wording.EMAIL_SIGN_OFF.upper())}</td></tr>",
        '<tr><td style="padding:6px 28px 0;">'
        f'<h1 style="margin:0;font-family:{FONT};font-size:24px;line-height:1.3;'
        f'font-weight:700;color:{INK};">{escape(email.heading)}</h1></td></tr>',
    ]
    rows += [_p(escape(line)) for line in email.lines]
    if email.when or email.where:
        rows.append(_details(email))
    if email.note:
        rows.append(_p(escape(email.note), top=16))
    for index, link in enumerate(email.calendar):
        rows.append(_button(link, primary=index == 0))
    if email.closing:
        rows.append(_p(_link(email.closing), top=20 if email.calendar else 16))
    rows.append(
        f'<tr><td style="padding:28px 28px 24px;font-family:{FONT};font-size:14px;'
        f'line-height:1.5;color:{MUTED};">{escape(wording.EMAIL_SIGN_OFF)}</td></tr>'
    )
    preheader = escape(email.when or (email.lines[0] if email.lines else email.heading))
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{escape(email.subject)}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE};">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{PAGE};"><tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:520px;background:#ffffff;border:1px solid {LINE};'
        f'border-radius:12px;">{"".join(rows)}</table>'
        "</td></tr></table></body></html>"
    )


def payload(email: wording.ProspectEmail) -> EmailPayload:
    return EmailPayload(subject=email.subject[:200], body=text(email), html=html(email))


# ---------------------------------------------------------------------------
# The calendar file
# ---------------------------------------------------------------------------


def _escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """RFC 5545 lines are at most 75 octets; longer ones continue after a space."""
    folded, current = [], ""
    for char in line:
        limit = 75 if not folded else 74
        if len((current + char).encode()) > limit:
            folded.append(current)
            current = char
        else:
            current += char
    folded.append(current)
    return [folded[0], *(f" {part}" for part in folded[1:])]


def ics(visit: wording.Visit, *, cancel_token: str, cancel_url: str, now: datetime) -> str:
    """The showing as an iCalendar file to add to a calendar. One event, no attendees,
    no organizer; its id is derived from the cancel token, never the token itself."""
    uid = hashlib.sha256(cancel_token.encode()).hexdigest()[:32]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sernia Capital//Showings//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}@{UID_DOMAIN}",
        f"DTSTAMP:{_stamp(now)}",
        f"DTSTART:{_stamp(visit.starts_at)}",
        f"DTEND:{_stamp(visit.ends_at)}",
        f"SUMMARY:{_escape_ics(wording.showing_event_title(visit))}",
        f"LOCATION:{_escape_ics(visit.address or f'unit {visit.unit}')}",
        f"DESCRIPTION:{_escape_ics(wording.showing_event_details(cancel_url))}",
        f"URL:{cancel_url}",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
