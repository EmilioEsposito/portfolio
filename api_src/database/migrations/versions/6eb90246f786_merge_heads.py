"""merge heads

Revision ID: 6eb90246f786
Revises: 7cb5319bb6b9, create_examples
Create Date: 2025-02-08 18:34:42.760714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6eb90246f786'
down_revision: Union[str, None] = ('7cb5319bb6b9', 'create_examples')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
