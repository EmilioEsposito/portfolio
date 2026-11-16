"""Showing bookings: the lead conversation whose reply booked a place.

- ``showing_attendees.lead_conversation_id``: set on a place the Zillow lead
  agent booked from a decision, to that decision's conversation. Saved in the
  booking's own transaction, so a reply that is then rejected, or never sent,
  can always find and cancel the place it booked, even when the process
  stopped before the conversation recorded it. Empty for every other booking.
- ``showing_attendees.lead_reply_sent_at``: when the reply that booked or
  confirmed the place was sent, saved in the step that releases its notices.
  A place with it is never cleaned up as unsent, whatever happens to the
  reply's card afterwards.
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_attendee_lead_conversation"
down_revision = "0047_zillow_lead_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "showing_attendees",
        sa.Column("lead_conversation_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_showing_attendees_lead_conversation_id",
        "showing_attendees",
        ["lead_conversation_id"],
    )
    op.add_column(
        "showing_attendees",
        sa.Column("lead_reply_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("showing_attendees", "lead_reply_sent_at")
    op.drop_index("ix_showing_attendees_lead_conversation_id", table_name="showing_attendees")
    op.drop_column("showing_attendees", "lead_conversation_id")
