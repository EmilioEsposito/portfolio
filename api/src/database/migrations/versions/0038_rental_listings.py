"""Rename rental_units to rental_listings.

The table holds the next lease on offer for a unit and what leads may be
told, not facts about the unit's current tenancy, so it is named for what it
holds. Constraints, indexes and sequences are renamed with it.
"""
from alembic import op

revision = '0038_rental_listings'
down_revision = '0037_rental_unit_offer_columns'
branch_labels = None
depends_on = None

_TABLES = [
    ('rental_units', 'rental_listings'),
    ('rental_unit_changes', 'rental_listing_changes'),
]
# Named by the metadata's naming convention (see database.py).
_CONSTRAINTS = [
    ('rental_listings', 'pk_rental_units', 'pk_rental_listings'),
    ('rental_listings', 'uq_rental_units_unit', 'uq_rental_listings_unit'),
    ('rental_listing_changes', 'pk_rental_unit_changes', 'pk_rental_listing_changes'),
    (
        'rental_listing_changes',
        'fk_rental_unit_changes_unit_id_rental_units',
        'fk_rental_listing_changes_listing_id_rental_listings',
    ),
]
_INDEXES = [
    ('ix_rental_unit_changes_unit_id', 'ix_rental_listing_changes_listing_id'),
    ('ix_rental_unit_changes_subject', 'ix_rental_listing_changes_subject'),
    ('ix_rental_unit_changes_changed_at', 'ix_rental_listing_changes_changed_at'),
]
_SEQUENCES = [
    ('rental_units_id_seq', 'rental_listings_id_seq'),
    ('rental_unit_changes_id_seq', 'rental_listing_changes_id_seq'),
]


def upgrade() -> None:
    for old, new in _TABLES:
        op.rename_table(old, new)
    op.alter_column('rental_listing_changes', 'unit_id', new_column_name='listing_id')
    for table, old, new in _CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}')
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX {old} RENAME TO {new}')
    for old, new in _SEQUENCES:
        op.execute(f'ALTER SEQUENCE {old} RENAME TO {new}')


def downgrade() -> None:
    for old, new in _SEQUENCES:
        op.execute(f'ALTER SEQUENCE {new} RENAME TO {old}')
    for old, new in _INDEXES:
        op.execute(f'ALTER INDEX {new} RENAME TO {old}')
    for table, old, new in _CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}')
    op.alter_column('rental_listing_changes', 'listing_id', new_column_name='unit_id')
    for old, new in _TABLES:
        op.rename_table(new, old)
