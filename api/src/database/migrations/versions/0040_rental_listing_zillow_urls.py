"""Add each rental listing's Zillow ad links: the public page and the editor page.

Empty strings rather than nulls, like ``showing_booking_url``: no link is "".
"""
import sqlalchemy as sa
from alembic import op

revision = '0040_rental_listing_zillow_urls'
down_revision = '0039_rental_listings_unit_case'
branch_labels = None
depends_on = None

_COLUMNS = ('zillow_listing_url_public_view', 'zillow_listing_url_internal_editor_view')


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            'rental_listings', sa.Column(name, sa.String(), nullable=False, server_default='')
        )


def downgrade() -> None:
    for name in _COLUMNS:
        op.drop_column('rental_listings', name)
