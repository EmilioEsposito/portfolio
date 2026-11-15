"""Showing bookings: calendar connections, showing agents, showings, attendees and notices.

All five tables now, so later steps of the booking work need few migrations.
The rules the database enforces are described in ``showing_bookings/models.py``.
``btree_gist`` lets one exclusion constraint compare an agent id with ``=`` and a
time range with ``&&``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_showing_bookings"
down_revision = "0041_drop_oauth_credentials"
branch_labels = None
depends_on = None

_EMPTY_LIST = sa.text("'[]'::jsonb")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _one_of(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(name))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("google_email", sa.String(), nullable=False),
        sa.Column("google_subject", sa.String(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), server_default=_EMPTY_LIST, nullable=False),
        sa.Column("encrypted_refresh_token", sa.String(), nullable=True),
        sa.Column("showings_calendar_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="connected", nullable=False),
        sa.Column("connected_by", sa.String(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_read_ok", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_connections")),
        sa.UniqueConstraint("google_subject", name=op.f("uq_calendar_connections_google_subject")),
        _one_of("ck_calendar_connections_status", "status", ("connected", "lost", "revoked")),
    )

    op.create_table(
        "showing_agents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("mobile", sa.String(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column(
            "busy_calendar_ids", postgresql.JSONB(), server_default=_EMPTY_LIST, nullable=False
        ),
        sa.Column("weekly_hours", postgresql.JSONB(), server_default=_EMPTY_LIST, nullable=False),
        sa.Column(
            "preferred_windows", postgresql.JSONB(), server_default=_EMPTY_LIST, nullable=False
        ),
        sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_showing_agents")),
        sa.UniqueConstraint("email", name=op.f("uq_showing_agents_email")),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["calendar_connections.id"],
            name=op.f("fk_showing_agents_connection_id_calendar_connections"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_showing_agents_connection_id"), "showing_agents", ["connection_id"])

    op.create_table(
        "showings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("address", sa.String(), server_default="", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="4", nullable=False),
        sa.Column("status", sa.String(), server_default="scheduled", nullable=False),
        sa.Column("cancel_reason", sa.String(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("host_event_id", sa.String(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_showings")),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["showing_agents.id"],
            name=op.f("fk_showings_agent_id_showing_agents"),
            ondelete="RESTRICT",
        ),
        _one_of("ck_showings_status", "status", ("scheduled", "cancelled", "completed")),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_showings_ends_after_start")),
        sa.CheckConstraint("capacity >= 1", name=op.f("ck_showings_capacity_positive")),
        # No two scheduled showings for one agent overlap. Half-open ranges, so
        # 4:00-4:30 and 4:30-5:00 can follow each other.
        postgresql.ExcludeConstraint(
            ("agent_id", "="),
            (
                sa.func.tstzrange(sa.column("starts_at"), sa.column("ends_at"), sa.text("'[)'")),
                "&&",
            ),
            using="gist",
            where=sa.text("status = 'scheduled'"),
            name="ex_showings_agent_overlap",
        ),
    )
    op.create_index(op.f("ix_showings_agent_id"), "showings", ["agent_id"])
    # One scheduled showing per unit and start time.
    op.create_index(
        "uq_showings_unit_starts_at_scheduled",
        "showings",
        ["unit", "starts_at"],
        unique=True,
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    op.create_table(
        "showing_attendees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("showing_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mobile", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="booked", nullable=False),
        sa.Column("attendance", sa.String(), server_default="unknown", nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("texts_accepted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("texts_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("texts_consent_version", sa.String(), nullable=True),
        sa.Column("texts_consent_page", sa.String(), nullable=True),
        sa.Column(
            "requirement_confirmations",
            postgresql.JSONB(),
            server_default=_EMPTY_LIST,
            nullable=False,
        ),
        sa.Column("zillow_thread_id", sa.String(), nullable=True),
        sa.Column("lead_token", sa.String(), nullable=True),
        sa.Column("cancel_token", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("signature_agent", sa.String(), nullable=True),
        sa.Column("calendar_event_id", sa.String(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_showing_attendees")),
        sa.ForeignKeyConstraint(
            ["showing_id"],
            ["showings.id"],
            name=op.f("fk_showing_attendees_showing_id_showings"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("cancel_token", name=op.f("uq_showing_attendees_cancel_token")),
        _one_of("ck_showing_attendees_status", "status", ("booked", "cancelled")),
        _one_of(
            "ck_showing_attendees_attendance", "attendance", ("unknown", "attended", "no_show")
        ),
        _one_of("ck_showing_attendees_source", "source", ("page", "team")),
    )
    op.create_index(op.f("ix_showing_attendees_showing_id"), "showing_attendees", ["showing_id"])
    op.create_index(
        op.f("ix_showing_attendees_zillow_thread_id"), "showing_attendees", ["zillow_thread_id"]
    )
    # One booked row per mobile number in a showing.
    op.create_index(
        "uq_showing_attendees_showing_id_mobile_booked",
        "showing_attendees",
        ["showing_id", "mobile"],
        unique=True,
        postgresql_where=sa.text("status = 'booked'"),
    )

    op.create_table(
        "showing_notices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("showing_id", sa.Integer(), nullable=False),
        sa.Column("attendee_id", sa.Integer(), nullable=True),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("recipient_key", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("guard_reason", sa.String(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sending_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_showing_notices")),
        sa.ForeignKeyConstraint(
            ["showing_id"],
            ["showings.id"],
            name=op.f("fk_showing_notices_showing_id_showings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attendee_id"],
            ["showing_attendees.id"],
            name=op.f("fk_showing_notices_attendee_id_showing_attendees"),
            ondelete="SET NULL",
        ),
        # No duplicate notices: one per kind, showing and recipient.
        sa.UniqueConstraint(
            "kind",
            "showing_id",
            "recipient_key",
            name=op.f("uq_showing_notices_kind_showing_id_recipient_key"),
        ),
        _one_of(
            "ck_showing_notices_channel", "channel", ("sms", "calendar_event", "calendar_invite")
        ),
        _one_of(
            "ck_showing_notices_status",
            "status",
            ("pending", "captured", "sent", "failed", "unknown", "skipped"),
        ),
        sa.CheckConstraint(
            "mode IS NULL OR mode IN ('off', 'capture', 'live')",
            name=op.f("ck_showing_notices_mode"),
        ),
    )
    op.create_index(op.f("ix_showing_notices_showing_id"), "showing_notices", ["showing_id"])
    op.create_index(
        "ix_showing_notices_pending_not_before",
        "showing_notices",
        ["not_before"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("showing_notices")
    op.drop_table("showing_attendees")
    op.drop_table("showings")
    op.drop_table("showing_agents")
    op.drop_table("calendar_connections")
    # btree_gist stays: dropping an extension other tables may use is not this migration's call.
