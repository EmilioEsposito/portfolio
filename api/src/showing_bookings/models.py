"""Tables for showing bookings. Migrations 0042 to 0046 build the same thing.

Three rules live in the database, so no code path can break them:

- one scheduled showing per unit and start time (a second person who picks
  the same time joins that showing as another attendee);
- no two scheduled showings overlap for one agent (an exclusion constraint;
  travel time between addresses is added in code, not here);
- one notice per kind, showing and recipient key, so a retry or a later
  attendee cannot text the same person twice.
"""

from datetime import datetime
from secrets import token_urlsafe

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base
from api.src.showing_bookings.schema import (
    ATTENDANCES,
    ATTENDEE_SOURCES,
    ATTENDEE_STATUSES,
    CHANNELS,
    CONNECTION_STATUSES,
    DEFAULT_CAPACITY,
    NOTICE_ATTENTIONS,
    NOTICE_STATUSES,
    SEND_MODES,
    SHOWING_STATUSES,
)

_EMPTY_LIST = text("'[]'::jsonb")


def _one_of(column_name: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    # Named by the metadata convention: ck_<table>_<column_name>.
    return CheckConstraint(f"{column_name} IN ({allowed})", name=column_name)


def new_cancel_token() -> str:
    """A random, unguessable token for an attendee's cancel link."""
    return token_urlsafe(24)


class CalendarConnection(Base):
    """One Google account that has granted calendar access, such as the team account.

    Its agents' busy times are read through it, and the showings calendar the
    app creates in that account holds the showing events.
    """

    __tablename__ = "calendar_connections"
    __table_args__ = (_one_of("status", CONNECTION_STATUSES),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    google_email: Mapped[str] = mapped_column(String)
    # Google's stable id for the account (the token's ``sub``); the email can change.
    google_subject: Mapped[str] = mapped_column(String, unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list, server_default=_EMPTY_LIST)
    # Encrypted with a key that exists only in production; never stored in the clear.
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    # The "Sernia showings" calendar the app created in this account.
    showings_calendar_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="connected", server_default="connected")
    connected_by: Mapped[str] = mapped_column(String)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Empty until the first read.
    last_read_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<CalendarConnection(id={self.id!r}, status={self.status!r})>"


class ShowingAgentRecord(Base):
    """A person who can host showings. Read and written as ``schema.ShowingAgent``."""

    __tablename__ = "showing_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)
    # Stored lowercased, so one person cannot become two agents.
    email: Mapped[str] = mapped_column(String, unique=True)
    mobile: Mapped[str | None] = mapped_column(String, nullable=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendar_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    busy_calendar_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default=_EMPTY_LIST)
    # Lists of ``PreferredWindow``, the shape listings already use.
    weekly_hours: Mapped[list] = mapped_column(JSONB, default=list, server_default=_EMPTY_LIST)
    preferred_windows: Mapped[list] = mapped_column(JSONB, default=list, server_default=_EMPTY_LIST)
    priority: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<ShowingAgentRecord(id={self.id!r}, name={self.name!r})>"


class Showing(Base):
    """One unit at one start time with one host; several prospects can attend."""

    __tablename__ = "showings"
    __table_args__ = (
        _one_of("status", SHOWING_STATUSES),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("capacity >= 1", name="capacity_positive"),
        # The unit is copied from the listing row, so its spelling is the listing's.
        Index(
            "uq_showings_unit_starts_at_scheduled",
            "unit",
            "starts_at",
            unique=True,
            postgresql_where=text("status = 'scheduled'"),
        ),
        ExcludeConstraint(
            ("agent_id", "="),
            (func.tstzrange(column("starts_at"), column("ends_at"), text("'[)'")), "&&"),
            using="gist",
            where=text("status = 'scheduled'"),
            name="ex_showings_agent_overlap",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The listing's unit key, such as "320-03" (``rental_listings.unit``).
    unit: Mapped[str] = mapped_column(String)
    # The address as it was when the showing was booked.
    address: Mapped[str] = mapped_column(String, default="", server_default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("showing_agents.id", ondelete="RESTRICT"), index=True
    )
    capacity: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_CAPACITY, server_default=str(DEFAULT_CAPACITY)
    )
    status: Mapped[str] = mapped_column(String, default="scheduled", server_default="scheduled")
    cancel_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The host's event on the showings calendar.
    host_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Why the residents could not be texted about this showing, such as
    # "lookup_failed" (``residents.PROBLEMS``); the team tells them instead.
    resident_attention: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Showing(id={self.id!r}, unit={self.unit!r}, starts_at={self.starts_at!r})>"


class ShowingAttendee(Base):
    """One prospect in one showing, with what they agreed to and how they booked."""

    __tablename__ = "showing_attendees"
    __table_args__ = (
        _one_of("status", ATTENDEE_STATUSES),
        _one_of("attendance", ATTENDANCES),
        _one_of("source", ATTENDEE_SOURCES),
        # A booked person appears once per showing: once per mobile number, and
        # once per Zillow thread for a lead booked before giving a number.
        Index(
            "uq_showing_attendees_showing_id_mobile_booked",
            "showing_id",
            "mobile",
            unique=True,
            postgresql_where=text("status = 'booked' AND mobile IS NOT NULL"),
        ),
        Index(
            "uq_showing_attendees_showing_id_zillow_thread_id_booked",
            "showing_id",
            "zillow_thread_id",
            unique=True,
            postgresql_where=text("status = 'booked' AND zillow_thread_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    showing_id: Mapped[int] = mapped_column(
        ForeignKey("showings.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String)
    # E.164. Required on the booking page; a lead the lead agent booked in an
    # email thread may not have given one yet.
    mobile: Mapped[str | None] = mapped_column(String, nullable=True)
    # Required on the booking page; a caller booked by the team may have none.
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="booked", server_default="booked")
    attendance: Mapped[str] = mapped_column(String, default="unknown", server_default="unknown")
    source: Mapped[str] = mapped_column(String)
    texts_accepted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # The consent record carriers ask a business to keep: when, which wording, which page.
    texts_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    texts_consent_version: Mapped[str | None] = mapped_column(String, nullable=True)
    texts_consent_page: Mapped[str | None] = mapped_column(String, nullable=True)
    # A list of ``schema.RequirementConfirmation``: key, the wording shown, and when.
    requirement_confirmations: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=_EMPTY_LIST
    )
    zillow_thread_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The lead conversation whose reply booked this place (source
    # ``lead_agent``), saved with the booking so a reply that is then rejected
    # or never sent can find and cancel it. Empty for every other booking.
    lead_conversation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The token in the link the lead agent sent, when the booking came through one.
    lead_token: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_token: Mapped[str] = mapped_column(String, unique=True, default=new_cancel_token)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    # The request's ``Signature-Agent`` header, when an agent signed it.
    signature_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    # The prospect's own event, which invites only them.
    calendar_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Why a cancelled attendee was cancelled, such as "number_refused" when Quo
    # refused the confirmation text and the booking was withdrawn.
    cancel_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set when the booking needs the team, such as "confirmation_undelivered";
    # empty when it does not.
    attention_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # How many times the mobile was corrected from the confirmation screen.
    mobile_corrections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # A keyed hash of the booking request's IP address, for the limit per
    # address. The address itself is not kept.
    client_ip_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<ShowingAttendee(id={self.id!r}, showing_id={self.showing_id!r})>"


class ShowingNotice(Base):
    """One text or calendar change owed because of a booking: the outbox.

    Written in the same transaction as the booking that owes it, then
    delivered by ``outbox.py``, the only code that hands a row to a provider.
    """

    __tablename__ = "showing_notices"
    __table_args__ = (
        _one_of("channel", CHANNELS),
        _one_of("status", NOTICE_STATUSES),
        _one_of("alert_reason", NOTICE_ATTENTIONS),
        CheckConstraint(
            "mode IS NULL OR mode IN (" + ", ".join(f"'{m}'" for m in SEND_MODES) + ")",
            name="mode",
        ),
        UniqueConstraint(
            "kind",
            "showing_id",
            "recipient_key",
            name="uq_showing_notices_kind_showing_id_recipient_key",
        ),
        Index(
            "ix_showing_notices_pending_not_before",
            "not_before",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # What the notice is for, such as "prospect_confirmation" or "resident_new_showing".
    kind: Mapped[str] = mapped_column(String)
    channel: Mapped[str] = mapped_column(String)
    showing_id: Mapped[int] = mapped_column(
        ForeignKey("showings.id", ondelete="CASCADE"), index=True
    )
    attendee_id: Mapped[int | None] = mapped_column(
        ForeignKey("showing_attendees.id", ondelete="SET NULL"), nullable=True
    )
    # The notice this one waits for: a prospect's confirmation text, which has
    # to be reported delivered before the calendar notices it holds go out.
    # Kept after release, as a record of what held it.
    waits_for_notice_id: Mapped[int | None] = mapped_column(
        ForeignKey("showing_notices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # A phone number in E.164 for a text, an email address for an email or a
    # calendar notice.
    recipient: Mapped[str] = mapped_column(String)
    # Who it is for, independent of how they are reached: "attendee:12",
    # "agent:3", "resident:<Quo contact id>". Part of the no-duplicates key.
    recipient_key: Mapped[str] = mapped_column(String)
    # ``schema.TextPayload`` or ``schema.CalendarPayload``, as rendered.
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="pending", server_default="pending")
    # The send mode when it was delivered, and why the guard chose what it did.
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    guard_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set, and committed, just before a provider is called; cleared when its
    # answer is saved. Still set much later means the send was interrupted.
    sending_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The one alert to the team about this notice (``alerts.py``): when, and
    # why, "failed" or "stuck". Set before the alert goes, so it never goes twice.
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<ShowingNotice(id={self.id!r}, kind={self.kind!r}, status={self.status!r})>"
