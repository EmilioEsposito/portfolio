"""Remove the retired listing and shared showing-booking overrides.

The application stopped selecting the column in the preceding release.
Rental listing change snapshots retain the values recorded at the time.
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_remove_booking_overrides"
down_revision = "0051_rental_listing_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("rental_listings", "showing_booking_url")
    op.execute(
        "UPDATE app_settings SET value = value - 'showing_booking_url' "
        "WHERE key = 'rental_showing_rules' AND value ? 'showing_booking_url'"
    )


def downgrade() -> None:
    op.add_column(
        "rental_listings",
        sa.Column("showing_booking_url", sa.String(), nullable=False, server_default=""),
    )
