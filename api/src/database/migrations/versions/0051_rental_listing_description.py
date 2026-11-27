"""Add each rental listing's description: the Zillow ad's text as published.

Several lines, copied from the ad as is. An empty string rather than a null,
like the other text fields: no description is "". No listing needs a value
filled in.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_rental_listing_description"
down_revision = "0050_notice_send_by_hand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rental_listings",
        sa.Column("listing_description", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("rental_listings", "listing_description")
