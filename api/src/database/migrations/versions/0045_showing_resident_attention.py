"""Showing bookings: a showing's mark when its residents could not be texted.

- ``showings.resident_attention``: why the residents of the unit were not
  texted about a new showing, such as a resident lookup that failed. The
  Booking settings tab shows it beside the showing.

A group text to roommates needs no column: its other numbers are on the
notice's payload.
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_showing_resident_attention"
down_revision = "0044_showing_public_booking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("showings", sa.Column("resident_attention", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("showings", "resident_attention")
