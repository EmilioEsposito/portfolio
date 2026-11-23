"""Showing agents: who can host showings, their hours, and the calendars that make them busy.

The team adds, changes and deactivates agents on the Booking settings tab
(``routes.py``). Every save goes through ``agent_problems``. An inactive
agent is kept, so the showings they hosted still name them, and gets no new
showings.

An agent's calendars are read through a calendar connection. An agent with no
connection of their own is read through the team's connection: the oldest
connection that is connected (``team_connection``). Saving no connection
therefore means "the team's", and an agent added before any calendar is
connected is read once one is.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.showing_bookings.models import CalendarConnection, ShowingAgentRecord
from api.src.showing_bookings.schema import ShowingAgent
from api.src.showing_bookings.seed import SAMPLE_AGENTS

MAX_NAME_LENGTH = 120
MAX_CALENDARS = 10
MAX_CALENDAR_ID_LENGTH = 200
MAX_WINDOWS = 14
MAX_PRIORITY = 99


class AgentInvalid(Exception):
    """An agent that must not be saved, with every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def is_calendar_address(calendar_id: str) -> bool:
    """Whether an agent may be saved with this calendar."""
    return (
        0 < len(calendar_id) <= MAX_CALENDAR_ID_LENGTH
        and calendar_id == calendar_id.strip()
        and not any(character.isspace() for character in calendar_id)
    )


def agent_problems(agent: ShowingAgent) -> list[str]:
    """Every reason this agent must not be saved. Empty means it may be."""
    problems: list[str] = []
    name = agent.name.strip()
    if not name or name != agent.name or "\n" in name:
        problems.append("name: empty, or has spaces around it")
    if len(name) > MAX_NAME_LENGTH:
        problems.append(f"name: over {MAX_NAME_LENGTH} characters")
    if not 1 <= agent.priority <= MAX_PRIORITY:
        problems.append(f"priority: must be from 1 to {MAX_PRIORITY}")
    if len(agent.busy_calendar_ids) > MAX_CALENDARS:
        problems.append(f"calendars: at most {MAX_CALENDARS}")
    for calendar_id in agent.busy_calendar_ids:
        if not is_calendar_address(calendar_id):
            problems.append(f"calendars: {calendar_id[:40]!r} is not a calendar address")
    if len({calendar_id.casefold() for calendar_id in agent.busy_calendar_ids}) < len(
        agent.busy_calendar_ids
    ):
        problems.append("calendars: a calendar is listed twice")
    for label, windows in (
        ("hours", agent.weekly_hours),
        ("preferred times", agent.preferred_windows),
    ):
        if len(windows) > MAX_WINDOWS:
            problems.append(f"{label}: at most {MAX_WINDOWS} windows")
        for window in windows:
            if window.start.tzinfo is not None or window.end.tzinfo is not None:
                problems.append(f"{label}: times are local, with no UTC offset")
            elif not window.days or window.start > window.end:
                problems.append(f"{label}: a window has no days, or starts after its last start")
    return problems


def as_agent(record: ShowingAgentRecord) -> ShowingAgent:
    """The row in the shape code reads."""
    return ShowingAgent.model_validate(
        {name: getattr(record, name) for name in ShowingAgent.model_fields}
    )


def _values(agent: ShowingAgent) -> dict:
    return agent.model_dump(mode="json")


async def list_agents(session: AsyncSession) -> list[ShowingAgentRecord]:
    """Every agent, active ones first, then by priority and name."""
    rows = await session.scalars(
        select(ShowingAgentRecord).order_by(
            ShowingAgentRecord.active.desc(),
            ShowingAgentRecord.priority,
            ShowingAgentRecord.name,
            ShowingAgentRecord.id,
        )
    )
    return list(rows)


async def _check(session: AsyncSession, agent: ShowingAgent, *, agent_id: int | None) -> None:
    problems = agent_problems(agent)
    if agent.connection_id is not None and (
        await session.get(CalendarConnection, agent.connection_id) is None
    ):
        problems.append("connection: no such calendar connection")
    same_email = await session.scalar(
        select(ShowingAgentRecord.id).where(ShowingAgentRecord.email == agent.email)
    )
    if same_email is not None and same_email != agent_id:
        problems.append(f"email: {agent.email} is already another agent's")
    if problems:
        raise AgentInvalid(problems)


async def add_agent(session: AsyncSession, agent: ShowingAgent) -> ShowingAgentRecord:
    """Save a new agent. Raises ``AgentInvalid`` and saves nothing when a check fails. Commits."""
    await _check(session, agent, agent_id=None)
    record = ShowingAgentRecord(**_values(agent))
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def change_agent(
    session: AsyncSession, agent_id: int, agent: ShowingAgent
) -> ShowingAgentRecord:
    """Replace an agent's details. Raises ``LookupError`` or ``AgentInvalid``. Commits."""
    record = await session.get(ShowingAgentRecord, agent_id, with_for_update=True)
    if record is None:
        raise LookupError(f"No showing agent {agent_id}.")
    await _check(session, agent, agent_id=agent_id)
    for name, value in _values(agent).items():
        setattr(record, name, value)
    await session.commit()
    await session.refresh(record)
    return record


async def set_active(session: AsyncSession, agent_id: int, active: bool) -> ShowingAgentRecord:
    """Deactivate an agent (no new showings) or make them active again. Commits."""
    record = await session.get(ShowingAgentRecord, agent_id, with_for_update=True)
    if record is None:
        raise LookupError(f"No showing agent {agent_id}.")
    record.active = active
    await session.commit()
    await session.refresh(record)
    return record


async def add_sample_agents(session: AsyncSession) -> list[str]:
    """Save the made-up agents in ``seed.SAMPLE_AGENTS`` that are not saved yet. Commits.

    The caller refuses this in production. Returns the names added.
    """
    added: list[str] = []
    for agent in SAMPLE_AGENTS:
        exists = await session.scalar(
            select(ShowingAgentRecord.id).where(ShowingAgentRecord.email == agent.email)
        )
        if exists is None:
            session.add(ShowingAgentRecord(**_values(agent)))
            added.append(agent.name)
    await session.commit()
    return added


def team_connection(connections: Sequence[CalendarConnection]) -> CalendarConnection | None:
    """The team's connection: the oldest one that is connected, or None."""
    connected = [row for row in connections if row.status == "connected"]
    return min(connected, key=lambda row: (row.connected_at, row.id), default=None)


def connection_for(
    agent: ShowingAgentRecord, connections: Sequence[CalendarConnection]
) -> CalendarConnection | None:
    """The connection that reads this agent's calendars: their own, or else the team's."""
    if agent.connection_id is not None:
        return next((row for row in connections if row.id == agent.connection_id), None)
    return team_connection(connections)
