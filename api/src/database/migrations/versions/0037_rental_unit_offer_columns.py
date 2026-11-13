"""Name the rental unit columns that describe the next lease on offer.

`rent` on a unit could be read as what the current resident pays. These
columns are the terms being offered for the next lease, and now say so.
"""
from alembic import op

revision = '0037_rental_unit_offer_columns'
down_revision = '0036_rental_units'
branch_labels = None
depends_on = None

RENAMES = {
    'rent': 'asking_rent',
    'lease_start_earliest': 'next_lease_start_earliest',
    'lease_start_latest': 'next_lease_start_latest',
    'virtual_tours': 'virtual_tours_allowed',
    'rented_on': 'off_market_on',
    'booking_url': 'showing_booking_url',
    'preferred_times': 'preferred_showing_times',
}


def upgrade() -> None:
    for old, new in RENAMES.items():
        op.alter_column('rental_units', old, new_column_name=new)


def downgrade() -> None:
    for old, new in RENAMES.items():
        op.alter_column('rental_units', new, new_column_name=old)
