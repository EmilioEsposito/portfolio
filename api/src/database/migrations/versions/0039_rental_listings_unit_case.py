"""Make rental listing unit names unique without regard to case.

Units are looked up by lower(unit). Two rows that differ only in case ("12-A"
and "12-a") made every lookup of that unit fail. No such rows can exist yet:
the table was created by this branch and production has no listings.
"""
import sqlalchemy as sa
from alembic import op

revision = '0039_rental_listings_unit_case'
down_revision = '0038_rental_listings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'uq_rental_listings_unit_lower',
        'rental_listings',
        [sa.text('lower(unit)')],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_rental_listings_unit_lower', table_name='rental_listings')
