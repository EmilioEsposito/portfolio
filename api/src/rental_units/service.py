"""Read and change rental units and the showing rules. The only code that writes them.

Every save is checked (``checks.py``) and recorded in the history in the same
transaction, so the current tables and the history cannot disagree. Callers
(the general agent's tools today; an edit screen and other services later)
decide who may change what. Nothing here asks for approval.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.rental_units.checks import RentalDataInvalid, rules_problems, unit_problems
from api.src.rental_units.models import RentalUnitChange, RentalUnitRecord
from api.src.rental_units.schema import Change, RentalUnit, ShowingRules
from api.src.sernia_ai.models import AppSetting

# The showing rules are one row of settings, not a table of their own.
SHOWING_RULES_KEY = "rental_showing_rules"
SHOWING_RULES_SUBJECT = "showing_rules"

_UNIT_FIELDS = tuple(RentalUnit.model_fields)


def _as_unit(record: RentalUnitRecord) -> RentalUnit:
    """The row as a checked unit. Raises ``RentalDataInvalid`` if it is not one."""
    try:
        unit = RentalUnit.model_validate({name: getattr(record, name) for name in _UNIT_FIELDS})
    except ValueError as error:
        raise RentalDataInvalid([f"unit {record.unit}: {error}"]) from error
    if problems := unit_problems(unit):
        raise RentalDataInvalid(problems)
    return unit


async def _record(
    session: AsyncSession, name: str, *, lock: bool = False
) -> RentalUnitRecord | None:
    query = select(RentalUnitRecord).where(
        func.lower(RentalUnitRecord.unit) == name.strip().lower()
    )
    return (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()


async def read_units(session: AsyncSession) -> list[RentalUnit]:
    """Every unit, by name. Raises ``RentalDataInvalid`` if any row fails its checks."""
    records = (
        (await session.execute(select(RentalUnitRecord).order_by(RentalUnitRecord.unit)))
        .scalars()
        .all()
    )
    return [_as_unit(record) for record in records]


async def read_unit(session: AsyncSession, name: str, *, lock: bool = False) -> RentalUnit | None:
    """One unit, matched without regard to case. ``lock`` holds the row until commit."""
    record = await _record(session, name, lock=lock)
    return _as_unit(record) if record else None


async def read_showing_rules(session: AsyncSession) -> ShowingRules:
    """The showing rules, or the defaults when none have been saved."""
    value = (
        await session.execute(select(AppSetting.value).where(AppSetting.key == SHOWING_RULES_KEY))
    ).scalar_one_or_none()
    if value is None:
        return ShowingRules()
    try:
        rules = ShowingRules.model_validate(value)
    except ValueError as error:
        raise RentalDataInvalid([f"showing rules: {error}"]) from error
    if problems := rules_problems(rules):
        raise RentalDataInvalid(problems)
    return rules


async def save_unit(
    session: AsyncSession, unit: RentalUnit, *, changed_by: str, source: str | None = None
) -> None:
    """Add the unit, or replace the one of the same name, and record the change.

    Raises ``RentalDataInvalid`` and saves nothing when a check fails. Commits.
    """
    if problems := unit_problems(unit):
        raise RentalDataInvalid(problems)
    values = unit.model_dump(mode="python")
    values["preferred_times"] = unit.model_dump(mode="json")["preferred_times"]
    record = await _record(session, unit.unit, lock=True)
    if record is None:
        record = RentalUnitRecord(**values)
        session.add(record)
    else:
        for name, value in values.items():
            setattr(record, name, value)
    await session.flush()  # the new row's id, for the history
    session.add(
        RentalUnitChange(
            unit_id=record.id,
            subject=unit.unit,
            snapshot=unit.model_dump(mode="json"),
            changed_by=changed_by,
            source=source,
        )
    )
    await session.commit()


async def save_showing_rules(
    session: AsyncSession, rules: ShowingRules, *, changed_by: str, source: str | None = None
) -> None:
    """Replace the showing rules and record the change. Commits."""
    if problems := rules_problems(rules):
        raise RentalDataInvalid(problems)
    snapshot = rules.model_dump(mode="json")
    await session.execute(
        pg_insert(AppSetting)
        .values(key=SHOWING_RULES_KEY, value=snapshot)
        .on_conflict_do_update(index_elements=["key"], set_={"value": snapshot})
    )
    session.add(
        RentalUnitChange(
            unit_id=None,
            subject=SHOWING_RULES_SUBJECT,
            snapshot=snapshot,
            changed_by=changed_by,
            source=source,
        )
    )
    await session.commit()


async def read_history(
    session: AsyncSession, subject: str | None = None, *, limit: int = 20
) -> list[Change]:
    """Saved changes, newest first: for one unit, the showing rules, or everything."""
    query = select(RentalUnitChange).order_by(
        RentalUnitChange.changed_at.desc(), RentalUnitChange.id.desc()
    )
    if subject is not None:
        query = query.where(func.lower(RentalUnitChange.subject) == subject.strip().lower())
    rows = (await session.execute(query.limit(limit))).scalars().all()
    return [
        Change(
            subject=row.subject,
            changed_at=row.changed_at,
            changed_by=row.changed_by,
            source=row.source,
            snapshot=row.snapshot,
        )
        for row in rows
    ]
