"""Tables for rental units and the history of changes to them."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


class RentalUnitRecord(Base):
    """One row per unit, as it stands now. Read and written as ``schema.RentalUnit``.

    A unit keeps its row from one season to the next: when it is listed
    again, the rent and dates change and the facts carry over. Earlier states
    are in ``rental_unit_changes``, so nothing reading this table has to pick
    between versions.
    """

    __tablename__ = "rental_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit: Mapped[str] = mapped_column(String, unique=True)
    address: Mapped[str] = mapped_column(String, default="")
    rent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_start_earliest: Mapped[date | None] = mapped_column(Date, nullable=True)
    lease_start_latest: Mapped[date | None] = mapped_column(Date, nullable=True)
    virtual_tours: Mapped[bool] = mapped_column(Boolean, default=False)
    rented_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    booking_url: Mapped[str] = mapped_column(String, default="")
    # Lists, validated by ``schema.RentalUnit`` on the way in and out.
    preferred_times: Mapped[list] = mapped_column(JSONB, default=list)
    public_facts: Mapped[list] = mapped_column(JSONB, default=list)
    internal_notes: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<RentalUnitRecord(unit={self.unit!r})>"


class RentalUnitChange(Base):
    """One row per saved change, never updated: the history.

    ``snapshot`` is the whole unit (or the showing rules) as it was after the
    change, so what was true on a given day is the latest row on or before it.
    """

    __tablename__ = "rental_unit_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Empty for a change to the showing rules, and for a unit since deleted.
    unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("rental_units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The unit's name at the time, or "showing_rules".
    subject: Mapped[str] = mapped_column(String, index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    changed_by: Mapped[str] = mapped_column(String)
    # Where the change came from: a conversation ID, a screen.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return f"<RentalUnitChange(subject={self.subject!r}, changed_at={self.changed_at!r})>"
