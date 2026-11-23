"""The calendars the showing agent form offers: those the team's connection can read.

``candidates`` reads Google afresh each time, through the connection that
reads an agent with none of their own (``agents.team_connection``):

1. The people in the Workspace directory, by their main calendar, and the
   calendars on the connected account's own list. The directory comes first:
   a calendar in both is in the team's group. The account's own calendar, the
   app's showings calendars and Google's virtual calendars (holidays,
   birthdays) are left out.
2. A free/busy read of each, over the next day. One that comes back is
   offered; one that does not is left out.

Every calendar saved on the agent is in the answer whatever happens: not
readable, in neither list, or not checked because Google could not be asked.
So the form never drops one by not showing it.

Each list says how its read went, and one that fails leaves the other as it
is. Nothing is written, kept or recorded on the connection, and no address or
name is logged.
"""

from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Literal

import logfire
from pydantic import BaseModel
from sqlalchemy import select

from api.src.database.database import AsyncSessionFactory
from api.src.showing_bookings import agents, connections, google_oauth
from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.showing_bookings.providers import CalendarList, ListedCalendar, ListStatus

# Ten free/busy reads' worth. Past it, the answer says it was cut.
MAX_CANDIDATES = 500
# Holidays, birthdays and the like, which Google makes up.
VIRTUAL_CALENDARS = "@group.v.calendar.google.com"
# Whether a calendar can be read is asked over this window.
READ_WINDOW = timedelta(days=1)


class CalendarCandidate(BaseModel):
    calendar_id: str
    # The person's or the calendar's name; the address when there is none.
    name: str
    # "team": in the Workspace directory. "shared": on the connected account's
    # calendar list. "saved": in neither, and saved on the agent.
    group: Literal["team", "shared", "saved"]
    # Whether a free/busy read returned it just now; empty when it was not checked.
    # Only a calendar saved on the agent is ever here without being readable.
    readable: bool | None


class CalendarCandidates(BaseModel):
    # "none": no account is connected. "lost": Google no longer accepts the
    # connection. "unusable": this environment cannot use it (``problem``).
    connection: Literal["usable", "none", "lost", "unusable"]
    # The connected account the calendars are read through.
    account: str | None
    problem: str | None = None
    # How each list was read; empty when Google was not asked.
    directory: ListStatus | None = None
    calendar_list: ListStatus | None = None
    # Google did not say whether some calendars can be read: trying again may work.
    check_failed: bool = False
    # There were more calendars than are offered here.
    truncated: bool = False
    calendars: list[CalendarCandidate]


def _saved(calendar_id: str, readable: bool | None = None) -> CalendarCandidate:
    return CalendarCandidate(
        calendar_id=calendar_id, name=calendar_id, group="saved", readable=readable
    )


def _without_google(
    connection: Literal["none", "lost", "unusable"],
    saved: Sequence[str],
    *,
    account: str | None = None,
    problem: str | None = None,
) -> CalendarCandidates:
    return CalendarCandidates(
        connection=connection,
        account=account,
        problem=problem,
        calendars=[_saved(calendar_id) for calendar_id in saved],
    )


def offered(
    directory: CalendarList, calendar_list: CalendarList, *, left_out: Collection[str]
) -> tuple[list[CalendarCandidate], bool]:
    """The calendars to check, each once and by name in its group, and whether there were more."""
    seen = {calendar_id.casefold() for calendar_id in left_out}

    def group(
        name: Literal["team", "shared"], entries: Sequence[ListedCalendar]
    ) -> list[CalendarCandidate]:
        found = []
        for entry in entries:
            key = entry.calendar_id.casefold()
            if (
                entry.own
                or key in seen
                or key.endswith(VIRTUAL_CALENDARS)
                # A save would refuse it.
                or not agents.is_calendar_address(entry.calendar_id)
            ):
                continue
            seen.add(key)
            found.append(
                CalendarCandidate(
                    calendar_id=entry.calendar_id, name=entry.name, group=name, readable=None
                )
            )
        return sorted(found, key=lambda item: (item.name.casefold(), item.calendar_id.casefold()))

    team, shared = group("team", directory.entries), group("shared", calendar_list.entries)
    # Calendars shared on purpose are kept before a large directory.
    shared_kept = shared[:MAX_CANDIDATES]
    team_kept = team[: MAX_CANDIDATES - len(shared_kept)]
    kept = team_kept + shared_kept
    cut = len(kept) < len(team) + len(shared)
    return kept, cut or directory.more or calendar_list.more


async def candidates(agent_id: int | None, *, now: datetime) -> CalendarCandidates:
    """The calendars to offer, with those saved on agent ``agent_id``, if one is given.

    Raises ``LookupError`` for no such agent.
    """
    async with AsyncSessionFactory() as session:
        rows = list(await session.scalars(select(CalendarConnection)))
        saved: list[str] = []
        if agent_id is not None:
            agent = await session.get(ShowingAgentRecord, agent_id)
            if agent is None:
                raise LookupError(f"No showing agent {agent_id}.")
            saved = list(agent.busy_calendar_ids)

    connection = agents.team_connection(rows)
    if connection is None:
        lost = [row for row in rows if row.status == "lost"]
        if not lost:
            return _without_google("none", saved)
        oldest = min(lost, key=lambda row: (row.connected_at, row.id))
        return _without_google("lost", saved, account=oldest.google_email)
    account = connection.google_email
    try:
        source = connections.picker_source(connection)
    except connections.ConnectionUnusable as unusable:
        return _without_google("unusable", saved, account=account, problem=str(unusable))

    granted = set(connection.scopes)
    try:
        directory = (
            await source.lists.directory()
            if google_oauth.DIRECTORY_SCOPE in granted
            else CalendarList("not_granted")
        )
        calendar_list = (
            await source.lists.calendar_list()
            if google_oauth.CALENDAR_LIST_SCOPE in granted
            else CalendarList("not_granted")
        )
        found, truncated = offered(
            directory,
            calendar_list,
            left_out=[
                account,
                *(row.showings_calendar_id for row in rows if row.showings_calendar_id),
            ],
        )
        # A saved calendar is asked about first, as it is saved.
        as_saved = {calendar_id.casefold(): calendar_id for calendar_id in saved}
        asked = [
            *as_saved.values(),
            *(c.calendar_id for c in found if c.calendar_id.casefold() not in as_saved),
        ]
        readable = await source.calendar.readable(asked, now, now + READ_WINDOW)
    except google_oauth.GrantLost:
        return _without_google("lost", saved, account=account)

    calendars = []
    for item in found:
        saved_id = as_saved.pop(item.calendar_id.casefold(), None)
        calendar_id = saved_id or item.calendar_id
        if saved_id is not None or readable.get(calendar_id):
            calendars.append(
                item.model_copy(
                    update={"calendar_id": calendar_id, "readable": readable.get(calendar_id)}
                )
            )
    calendars += [
        _saved(calendar_id, readable.get(calendar_id)) for calendar_id in as_saved.values()
    ]
    check_failed = any(calendar_id not in readable for calendar_id in asked)
    logfire.info(
        "showing bookings: calendar candidates read",
        connection_id=connection.id,
        directory=directory.status,
        calendar_list=calendar_list.status,
        found=len(found),
        offered=len(calendars),
        unreadable=sum(item.readable is False for item in calendars),
        check_failed=check_failed,
        truncated=truncated,
    )
    return CalendarCandidates(
        connection="usable",
        account=account,
        directory=directory.status,
        calendar_list=calendar_list.status,
        check_failed=check_failed,
        truncated=truncated,
        calendars=calendars,
    )
