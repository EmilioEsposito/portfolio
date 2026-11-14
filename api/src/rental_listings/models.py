"""Tables for rental listings and the history of changes to them."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


class RentalListingRecord(Base):
    """One row per unit's listing, as it stands now. Read and written as ``schema.RentalListing``.

    A unit has one listing and keeps it from one season to the next: when the
    unit goes back on offer, the asking rent and dates change and the facts
    carry over. Earlier states are in ``rental_listing_changes``, so nothing
    reading this table has to pick between versions.
    """

    __tablename__ = "rental_listings"
    # Units are matched without regard to case, so they are unique that way too.
    # The same expression as migration 0039, so autogenerate sees no difference.
    __table_args__ = (Index("uq_rental_listings_unit_lower", text("lower(unit)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit: Mapped[str] = mapped_column(String, unique=True)
    address: Mapped[str] = mapped_column(String, default="")
    asking_rent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_lease_start_earliest: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_lease_start_latest: Mapped[date | None] = mapped_column(Date, nullable=True)
    virtual_tours_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    off_market_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    showing_booking_url: Mapped[str] = mapped_column(String, default="")
    zillow_listing_url_public_view: Mapped[str] = mapped_column(String, default="")
    zillow_listing_url_internal_editor_view: Mapped[str] = mapped_column(String, default="")
    # Lists, validated by ``schema.RentalListing`` on the way in and out.
    preferred_showing_times: Mapped[list] = mapped_column(JSONB, default=list)
    public_facts: Mapped[list] = mapped_column(JSONB, default=list)
    internal_notes: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<RentalListingRecord(unit={self.unit!r})>"


class RentalListingChange(Base):
    """One row per saved change, never updated: the history.

    ``snapshot`` is the whole listing (or the showing rules) as it was after the
    change, so what was true on a given day is the latest row on or before it.
    """

    __tablename__ = "rental_listing_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Empty for a change to the showing rules, and for a listing since deleted.
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("rental_listings.id", ondelete="SET NULL"), nullable=True, index=True
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
        return f"<RentalListingChange(subject={self.subject!r}, changed_at={self.changed_at!r})>"
