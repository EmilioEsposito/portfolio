"""What a rental listing or the showing rules must pass before they are saved or used.

The types in ``schema.py`` say what shape a value has. These checks cover the
rest: values that make no sense, and free text that must not be stored here.

The text checks apply to public facts and internal notes alike. The Zillow
lead agent reads both and answers strangers, so neither may hold anything
that would matter if it leaked: a phone number, an email address, a link, or
words about access codes. They catch the obvious cases. What they cannot
catch (a wrong fact, a note that is more sensitive than it looks) is why a
change to free text made by an agent waits for a person's approval.
"""

import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from api.src.rental_listings.schema import RentalListing, ShowingRules

MAX_LINES = 60  # public facts, or internal notes, per listing
MAX_LINE_LENGTH = 300
MAX_NAME_LENGTH = 120

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_LINK = re.compile(r"https?://|www\.", re.I)
# No word boundaries: "frontdoor code", "keycode" and "lockbox4821" must match
# too. A rare false positive ("monkey code") is refused and can be reworded.
_ACCESS = re.compile(
    r"lock\s*box|pass\s*(?:code|word)"
    r"|(?:door|gate|access|alarm|entry|garage|key|keypad)\s*code",
    re.I,
)


class RentalDataInvalid(Exception):
    """A listing or the showing rules that must not be saved or used, with every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _text_problems(where: str, text: str) -> list[str]:
    """Reasons a piece of free text may not be stored."""
    found = []
    if _EMAIL.search(text):
        found.append(f"{where}: holds an email address")
    if _PHONE.search(text):
        found.append(f"{where}: holds a phone number")
    if _LINK.search(text):
        found.append(f"{where}: holds a link (links go in showing_booking_url only)")
    if _ACCESS.search(text):
        found.append(f"{where}: mentions an access code, lockbox or password")
    return found


def _booking_url_problems(where: str, url: str) -> list[str]:
    if not url:
        return []
    parts = urlsplit(url)
    plain = parts.scheme == "https" and parts.hostname and not parts.username
    if not plain or any(character.isspace() for character in url):
        return [f"{where}: must be a plain https address"]
    return []


def _lines_problems(where: str, kind: str, lines: list[str]) -> list[str]:
    problems = []
    if len(lines) > MAX_LINES:
        problems.append(f"{where}: more than {MAX_LINES} {kind}s")
    for line in lines:
        if not line.strip() or "\n" in line:
            problems.append(f"{where}: a {kind} is empty or spans more than one line")
        if len(line) > MAX_LINE_LENGTH:
            problems.append(f"{where}: a {kind} is over {MAX_LINE_LENGTH} characters")
        problems += _text_problems(f"{where} {kind} {line[:40]!r}", line)
    return problems


def listing_problems(listing: RentalListing) -> list[str]:
    """Every reason this listing must not be saved or used. Empty means it may be."""
    name = listing.unit.strip()
    where = f"unit {name or '(unnamed)'}"
    problems: list[str] = []
    if not name or name != listing.unit:
        problems.append(f"{where}: the name is empty or has spaces around it")
    if len(name) > MAX_NAME_LENGTH or len(listing.address) > MAX_NAME_LENGTH:
        problems.append(f"{where}: name or address is over {MAX_NAME_LENGTH} characters")
    if listing.asking_rent is not None and listing.asking_rent <= 0:
        problems.append(f"{where}: asking_rent must be more than 0")
    first, final = listing.next_lease_start_earliest, listing.next_lease_start_latest
    if first and final and first > final:
        problems.append(f"{where}: next_lease_start_earliest is after next_lease_start_latest")
    problems += _booking_url_problems(f"{where} showing_booking_url", listing.showing_booking_url)
    problems += _text_problems(f"{where} name", listing.unit)
    problems += _text_problems(f"{where} address", listing.address)
    problems += _lines_problems(where, "public fact", listing.public_facts)
    problems += _lines_problems(where, "internal note", listing.internal_notes)
    for preferred in listing.preferred_showing_times:
        if not preferred.days or preferred.start > preferred.end:
            problems.append(f"{where}: a preferred time has no days, or starts after it ends")
    return problems


def rules_problems(rules: ShowingRules) -> list[str]:
    """Every reason these showing rules must not be saved or used."""
    problems: list[str] = []
    try:
        ZoneInfo(rules.timezone)
    except Exception:
        problems.append(f"timezone: {rules.timezone!r} is not a time zone")
    if rules.earliest_start > rules.latest_start:
        problems.append("showing rules: earliest_start is after latest_start")
    problems += _booking_url_problems(
        "showing rules showing_booking_url", rules.showing_booking_url
    )
    return problems
