"""Where production keeps operating data: one JSON file in the general agent's workspace.

The lead agent's case needs what is true of the business right now: the
listings and a fact sheet for each, which units are rented, booking links, the
outer showing window, blackout dates and each unit's preferred times. That is
``OperatingData`` (``case.py``), and in production it is the file
``OPERATING_FILE`` in the workspace, a git-backed directory:

- **A person** edits it on GitHub (the sernia-knowledge repo), where every
  change is a dated commit.
- **The general agent** changes it with the tools in ``data_tools.py`` when
  told something changed. It cannot write the file with its ordinary file
  tools (``workspace_tools.py`` refuses), so every change it makes passes the
  checks below, and a change to free text waits for a person's approval.
- **Code** reads it here, once per lead, after pulling the workspace.

Tour times and booked tours are not in this file. They come from a
``TourSource`` (``tours.py``).

Two things are checked on every read and every write:

- **Shape.** The file must parse as ``OperatingData`` with no unknown keys. A
  misspelt key would otherwise be dropped silently, and a unit marked rented
  under the wrong name would go on being offered.
- **Public facts only.** The lead agent answers strangers and is built to
  hold nothing private, so nothing private may reach it by this route. Free
  text is refused when it holds a phone number, an email address, a link, or
  words about access codes; a booking link must be a plain ``https`` address.
  This catches the obvious cases. What it cannot catch (a resident's name, a
  wrong fact) is why the general agent's free-text changes need approval.

A file that fails either check is not used at all. ``load`` reports the
problem, and ``handle.py`` hands every lead to a person until the file is
fixed. Nothing here falls back to an older version: answering from facts one
edit behind is worse than asking a person.
"""

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import logfire
from pydantic import ValidationError

from api.src.sernia_ai.config import ZILLOW_OPERATING_FILE
from api.src.sernia_ai.memory.git_sync import pull_workspace
from api.src.sernia_ai.zillow_leads.case import OperatingData

# Relative to the workspace root.
OPERATING_FILE = ZILLOW_OPERATING_FILE

# A pull that takes longer than this is left to finish on its own, and the
# case is built from the copy already on disk.
PULL_TIMEOUT_SECONDS = 20

MAX_LISTINGS = 50
MAX_FACTS_PER_LISTING = 60
MAX_FACT_LENGTH = 300
MAX_NAME_LENGTH = 120

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_LINK = re.compile(r"https?://|www\.", re.I)
_ACCESS = re.compile(
    r"\b(lock\s?box|pass\s?code|password"
    r"|(?:door|gate|access|alarm|entry|garage|key|keypad)\s+code)\b",
    re.I,
)


class OperatingDataInvalid(Exception):
    """Operating data that must not be used, with every reason found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Loaded:
    """Operating data as read, or the reason there is none.

    ``data`` is empty when the file is missing or failed its checks. ``problem``
    is set only in the second case: the file is there and must not be used.
    """

    data: OperatingData = field(default_factory=OperatingData)
    found: bool = False
    problem: str | None = None


def operating_file(workspace: Path) -> Path:
    return workspace / OPERATING_FILE


def _private_text_problems(where: str, text: str) -> list[str]:
    """Reasons a piece of free text may not be shown to a lead."""
    found = []
    if _EMAIL.search(text):
        found.append(f"{where}: holds an email address")
    if _PHONE.search(text):
        found.append(f"{where}: holds a phone number")
    if _LINK.search(text):
        found.append(f"{where}: holds a link (links go in booking_url only)")
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


def problems_in(operating: OperatingData) -> list[str]:
    """Every reason this operating data must not reach the lead agent.

    Checks what the types cannot: values that make no sense, and free text
    that is not a public fact. An empty list means it may be used.
    """
    problems: list[str] = []
    try:
        ZoneInfo(operating.timezone)
    except Exception:
        problems.append(f"timezone: {operating.timezone!r} is not a time zone")
    window = operating.showing_window
    if window.earliest_start > window.latest_start:
        problems.append("showing_window: earliest_start is after latest_start")
    problems += _booking_url_problems("booking_url", operating.booking_url)
    if len(operating.listings) > MAX_LISTINGS:
        problems.append(f"listings: more than {MAX_LISTINGS}")
    for position, unit in enumerate(operating.rented_units):
        problems += _private_text_problems(f"rented_units[{position}]", unit)

    seen: set[str] = set()
    for listing in operating.listings:
        name = listing.unit.strip()
        where = f"listing {name or '(unnamed)'}"
        if not name:
            problems.append(f"{where}: unit is empty")
        if name.casefold() in seen:
            problems.append(f"{where}: listed more than once")
        seen.add(name.casefold())
        if len(name) > MAX_NAME_LENGTH or len(listing.address) > MAX_NAME_LENGTH:
            problems.append(f"{where}: unit or address is over {MAX_NAME_LENGTH} characters")
        if listing.rent is not None and listing.rent <= 0:
            problems.append(f"{where}: rent must be more than 0")
        first, final = listing.lease_start_earliest, listing.lease_start_latest
        if first and final and first > final:
            problems.append(f"{where}: lease_start_earliest is after lease_start_latest")
        problems += _booking_url_problems(f"{where} booking_url", listing.booking_url)
        problems += _private_text_problems(f"{where} unit", listing.unit)
        problems += _private_text_problems(f"{where} address", listing.address)
        facts = listing.facts.splitlines()
        if len(facts) > MAX_FACTS_PER_LISTING:
            problems.append(f"{where}: more than {MAX_FACTS_PER_LISTING} facts")
        for line in facts:
            if len(line) > MAX_FACT_LENGTH:
                problems.append(f"{where}: a fact is over {MAX_FACT_LENGTH} characters")
            problems += _private_text_problems(f"{where} fact {line[:40]!r}", line)
        for preferred in listing.preferred_times:
            if not preferred.days or preferred.start > preferred.end:
                problems.append(f"{where}: a preferred time has no days, or starts after it ends")
    return problems


def parse(text: str) -> OperatingData:
    """Operating data from the file's text, or ``OperatingDataInvalid``."""
    try:
        operating = OperatingData.model_validate_json(text, extra="forbid")
    except ValidationError as error:
        raise OperatingDataInvalid(
            [
                f"{'.'.join(str(part) for part in item['loc']) or 'file'}: {item['msg']}"
                for item in error.errors()
            ]
        ) from error
    if problems := problems_in(operating):
        raise OperatingDataInvalid(problems)
    return operating


def to_file_text(operating: OperatingData) -> str:
    """The file as it is written: fact sheets as lists of lines, for editing by hand."""
    document = operating.model_dump(mode="json")
    for listing in document["listings"]:
        listing["facts"] = listing["facts"].splitlines()
    if not document["availability"]:
        del document["availability"]  # hand-kept tour times; production does not read them
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def read(workspace: Path) -> Loaded:
    """Operating data from the copy on disk. Never raises."""
    path = operating_file(workspace)
    try:
        if not path.is_file():
            return Loaded()
        return Loaded(data=parse(path.read_text()), found=True)
    except OperatingDataInvalid as error:
        return Loaded(found=True, problem=str(error))
    except Exception as error:  # unreadable bytes, a permissions error
        return Loaded(found=True, problem=f"could not be read: {error}")


def write(workspace: Path, operating: OperatingData) -> None:
    """Replace the file, after the same checks a read applies.

    Raises ``OperatingDataInvalid`` and writes nothing when a check fails. The
    file is replaced in one step, so a reader never sees half of it.
    """
    text = to_file_text(operating)
    parse(text)  # what is written is what will be read back
    path = operating_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".operating-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


async def load(workspace: Path) -> Loaded:
    """Operating data as it stands now, for one lead's case. Never raises.

    Pulls the workspace first, so an edit made on GitHub is seen by the next
    lead. A pull that fails or is slow is not fatal: the copy on disk is used.
    """
    try:
        pull = asyncio.ensure_future(pull_workspace(workspace))
        # Shielded: a pull stopped halfway would leave git's lock behind.
        await asyncio.wait_for(asyncio.shield(pull), PULL_TIMEOUT_SECONDS)
    except TimeoutError:
        logfire.warn("zillow lead: workspace pull is slow, using operating data on disk")
    except Exception:
        logfire.exception("zillow lead: workspace pull failed, using operating data on disk")

    loaded = await asyncio.to_thread(read, workspace)
    if loaded.problem:
        logfire.error(
            "zillow lead: operating data is not usable, every lead goes to a person: {problem}",
            problem=loaded.problem,
            file=OPERATING_FILE,
        )
    elif not loaded.found:
        logfire.warn("zillow lead: no operating data file, the agent has no listings")
    return loaded
