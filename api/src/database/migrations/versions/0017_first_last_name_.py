"""

Revision ID: 0017_first_last_name
Revises: 0016_contact_openphone_col
Create Date: 2025-05-13 20:30:37.177618

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0017_first_last_name'
down_revision: Union[str, None] = '0016_contact_openphone_col'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add new columns as nullable
    op.add_column('contacts', sa.Column('first_name', sa.String(), nullable=True))
    op.add_column('contacts', sa.Column('last_name', sa.String(), nullable=True))

    # 2. Backfill data using SQL
    op.execute("""
        UPDATE contacts
        SET
            first_name = split_part(name, ' ', 1),
            last_name = CASE
                WHEN strpos(name, ' ') > 0 THEN substring(name from position(' ' in name) + 1)
                ELSE ''
            END
    """)

    # 3. Set NOT NULL constraint
    op.alter_column('contacts', 'first_name', nullable=False)
    op.alter_column('contacts', 'last_name', nullable=False)

    # 4. Drop the old column
    op.drop_column('contacts', 'name')


def downgrade() -> None:
    # 1. Add the old 'name' column back as nullable
    op.add_column('contacts', sa.Column('name', sa.String(), nullable=True))

    # 2. Backfill 'name' by joining first_name and last_name
    op.execute("""
        UPDATE contacts
        SET name = 
            CASE
                WHEN last_name IS NOT NULL AND last_name != ''
                    THEN first_name || ' ' || last_name
                ELSE first_name
            END
    """)

    # 3. Set 'name' to NOT NULL
    op.alter_column('contacts', 'name', nullable=False)

    # 4. Drop the new columns
    op.drop_column('contacts', 'last_name')
    op.drop_column('contacts', 'first_name')
