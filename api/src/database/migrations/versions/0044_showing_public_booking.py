"""Showing bookings from the public page: what an attendee row and a notice row now need.

- ``showing_attendees.cancel_reason``: why a cancelled attendee was cancelled,
  such as a withdrawn booking whose number Quo refused.
- ``showing_attendees.attention_reason``: set when a booking needs the team,
  such as a confirmation text reported undelivered.
- ``showing_attendees.mobile_corrections``: corrections made from the
  confirmation screen, at most two per booking.
- ``showing_attendees.client_ip_hash``: a keyed hash of the booking request's
  IP address, for the limit per address.
- ``showing_notices.waits_for_notice_id``: the confirmation text a held
  notice waits for, and the statuses ``held`` and ``cancelled``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_showing_public_booking"
down_revision = "0043_listing_showing_notice"
branch_labels = None
depends_on = None

_OLD_STATUSES = ("pending", "captured", "sent", "failed", "unknown", "skipped")
_NEW_STATUSES = (*_OLD_STATUSES, "held", "cancelled")


def _status_check(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(op.f("ck_showing_notices_status"), "showing_notices", type_="check")
    op.create_check_constraint(
        op.f("ck_showing_notices_status"), "showing_notices", f"status IN ({allowed})"
    )


def upgrade() -> None:
    op.add_column("showing_attendees", sa.Column("cancel_reason", sa.String(), nullable=True))
    op.add_column("showing_attendees", sa.Column("attention_reason", sa.String(), nullable=True))
    op.add_column(
        "showing_attendees",
        sa.Column("mobile_corrections", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("showing_attendees", sa.Column("client_ip_hash", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_showing_attendees_client_ip_hash"), "showing_attendees", ["client_ip_hash"]
    )
    op.add_column("showing_notices", sa.Column("waits_for_notice_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_showing_notices_waits_for_notice_id_showing_notices"),
        "showing_notices",
        "showing_notices",
        ["waits_for_notice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_showing_notices_waits_for_notice_id"), "showing_notices", ["waits_for_notice_id"]
    )
    _status_check(_NEW_STATUSES)


def downgrade() -> None:
    op.execute(
        "UPDATE showing_notices SET status = 'skipped' WHERE status IN ('held', 'cancelled')"
    )
    _status_check(_OLD_STATUSES)
    op.drop_index(op.f("ix_showing_notices_waits_for_notice_id"), table_name="showing_notices")
    op.drop_constraint(
        op.f("fk_showing_notices_waits_for_notice_id_showing_notices"),
        "showing_notices",
        type_="foreignkey",
    )
    op.drop_column("showing_notices", "waits_for_notice_id")
    op.drop_index(op.f("ix_showing_attendees_client_ip_hash"), table_name="showing_attendees")
    op.drop_column("showing_attendees", "client_ip_hash")
    op.drop_column("showing_attendees", "mobile_corrections")
    op.drop_column("showing_attendees", "attention_reason")
    op.drop_column("showing_attendees", "cancel_reason")
