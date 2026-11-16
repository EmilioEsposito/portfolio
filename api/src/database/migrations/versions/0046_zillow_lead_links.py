"""Zillow leads: each lead thread's own booking-link token.

- ``zillow_lead_links``: one row per lead thread (its shared-mailbox thread
  id), with a random token that holds no personal data. The lead agent's
  booking links carry it as ``?t=``, and a booking made through one is tied
  to the thread (``showing_attendees.zillow_thread_id``).
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_zillow_lead_links"
down_revision = "0045_showing_resident_attention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zillow_lead_links",
        sa.Column("thread_id", sa.String(), primary_key=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token", name="uq_zillow_lead_links_token"),
    )


def downgrade() -> None:
    op.drop_table("zillow_lead_links")
