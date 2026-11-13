"""Rental units and the history of changes to them."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0036_rental_units'
down_revision = '0035_sernia_model_gpt6_luna'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rental_units',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('unit', sa.String(), nullable=False, unique=True),
        sa.Column('address', sa.String(), nullable=False, server_default=''),
        sa.Column('rent', sa.Integer()),
        sa.Column('lease_start_earliest', sa.Date()),
        sa.Column('lease_start_latest', sa.Date()),
        sa.Column('virtual_tours', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('rented_on', sa.Date()),
        sa.Column('booking_url', sa.String(), nullable=False, server_default=''),
        sa.Column(
            'preferred_times', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            'public_facts', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            'internal_notes', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        'rental_unit_changes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('unit_id', sa.Integer(), sa.ForeignKey('rental_units.id', ondelete='SET NULL')),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('changed_by', sa.String(), nullable=False),
        sa.Column('source', sa.String()),
        sa.Column(
            'changed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index('ix_rental_unit_changes_unit_id', 'rental_unit_changes', ['unit_id'])
    op.create_index('ix_rental_unit_changes_subject', 'rental_unit_changes', ['subject'])
    op.create_index('ix_rental_unit_changes_changed_at', 'rental_unit_changes', ['changed_at'])


def downgrade() -> None:
    op.drop_table('rental_unit_changes')
    op.drop_table('rental_units')
