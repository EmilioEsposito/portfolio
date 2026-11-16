"""Add each rental listing's own showing notice, in hours.

Empty uses the notice in the showing rules (``rental_showing_rules`` in
``app_settings``, 24 hours unless changed), which needs no migration: a saved
rules row without the field reads as the default.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_listing_showing_notice"
down_revision = "0042_showing_bookings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rental_listings", sa.Column("showing_notice_hours", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("rental_listings", "showing_notice_hours")
