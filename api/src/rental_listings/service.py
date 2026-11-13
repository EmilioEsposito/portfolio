"""Read and change rental listings and the showing rules. The only code that writes them.

Every save is checked (``checks.py``) and recorded in the history in the same
transaction, so the current tables and the history cannot disagree. Callers
(the general agent's tools today; an edit screen and other services later)
decide who may change what. Nothing here asks for approval.
"""

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.rental_listings.checks import RentalDataInvalid, listing_problems, rules_problems
from api.src.rental_listings.models import RentalListingChange, RentalListingRecord
from api.src.rental_listings.schema import Change, RentalListing, ShowingRules
from api.src.sernia_ai.models import AppSetting

# The showing rules are one row of settings, not a table of their own.
SHOWING_RULES_KEY = "rental_showing_rules"
SHOWING_RULES_SUBJECT = "showing_rules"

_LISTING_FIELDS = tuple(RentalListing.model_fields)


def _as_listing(record: RentalListingRecord) -> RentalListing:
    """The row as a checked listing. Raises ``RentalDataInvalid`` if it is not one."""
    try:
        listing = RentalListing.model_validate(
            {name: getattr(record, name) for name in _LISTING_FIELDS}
        )
    except ValueError as error:
        raise RentalDataInvalid([f"unit {record.unit}: {error}"]) from error
    if problems := listing_problems(listing):
        raise RentalDataInvalid(problems)
    return listing


async def _record(
    session: AsyncSession, name: str, *, lock: bool = False
) -> RentalListingRecord | None:
    key = name.strip().lower()
    if lock:
        # A row lock cannot hold a unit that has no listing yet, so two callers
        # creating "12-A" and "12-a" at once would both find nothing and both
        # insert. This lock is on the name itself, held until commit.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"rental_listing:{key}"},
        )
    query = select(RentalListingRecord).where(func.lower(RentalListingRecord.unit) == key)
    return (await session.execute(query.with_for_update() if lock else query)).scalar_one_or_none()


async def read_listings(session: AsyncSession) -> list[RentalListing]:
    """Every listing, by unit name. Raises ``RentalDataInvalid`` if any row fails its checks."""
    records = (
        (await session.execute(select(RentalListingRecord).order_by(RentalListingRecord.unit)))
        .scalars()
        .all()
    )
    return [_as_listing(record) for record in records]


async def read_listing(
    session: AsyncSession, name: str, *, lock: bool = False
) -> RentalListing | None:
    """One unit's listing, matched without regard to case.

    ``lock`` holds that unit, listed or not, until the session commits, so a
    caller that reads, changes and saves it cannot race another.
    """
    record = await _record(session, name, lock=lock)
    return _as_listing(record) if record else None


async def read_showing_rules(session: AsyncSession, *, lock: bool = False) -> ShowingRules:
    """The showing rules, or the defaults when none have been saved.

    ``lock`` holds the showing rules until the session commits, so a caller
    that reads, changes and saves them cannot overwrite a change made in
    between. It is a transaction-level advisory lock, not a row lock, because
    before the first save there is no row to lock.
    """
    if lock:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": SHOWING_RULES_KEY}
        )
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


async def save_listing(
    session: AsyncSession, listing: RentalListing, *, changed_by: str, source: str | None = None
) -> None:
    """Add the listing, or replace the one for the same unit, and record the change.

    Raises ``RentalDataInvalid`` and saves nothing when a check fails. Commits.
    """
    if problems := listing_problems(listing):
        raise RentalDataInvalid(problems)
    values = listing.model_dump(mode="python")
    values["preferred_showing_times"] = listing.model_dump(mode="json")["preferred_showing_times"]
    record = await _record(session, listing.unit, lock=True)
    if record is None:
        record = RentalListingRecord(**values)
        session.add(record)
    else:
        for name, value in values.items():
            setattr(record, name, value)
    await session.flush()  # the new row's id, for the history
    session.add(
        RentalListingChange(
            listing_id=record.id,
            subject=listing.unit,
            snapshot=listing.model_dump(mode="json"),
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
        RentalListingChange(
            listing_id=None,
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
    """Saved changes, newest first: for one unit's listing, the showing rules, or everything."""
    query = select(RentalListingChange).order_by(
        RentalListingChange.changed_at.desc(), RentalListingChange.id.desc()
    )
    if subject is not None:
        query = query.where(func.lower(RentalListingChange.subject) == subject.strip().lower())
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
