"""Showing bookings: when a lead agent's reply may or may not have gone out.

``showing_attendees.lead_reply_uncertain_at``: set on a place the Zillow lead
agent booked when the send of its reply had no certain answer. Until now only
``attention_reason`` (``reply_uncertain``) said so, and that is one value the
team reads: a place that keeps the lead's details (a moved showing) can also
be marked because its confirmation text did not reach the number, which would
replace it. A place whose reply may have gone out is never cleaned up as
unsent, so the state has a column of its own, beside ``lead_reply_sent_at``.

Rows already marked ``reply_uncertain`` get it, at the time they last changed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_lead_reply_uncertain"
down_revision = "0048_attendee_lead_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "showing_attendees",
        sa.Column("lead_reply_uncertain_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE showing_attendees SET lead_reply_uncertain_at = updated_at "
        "WHERE attention_reason = 'reply_uncertain'"
    )


def downgrade() -> None:
    op.drop_column("showing_attendees", "lead_reply_uncertain_at")
