"""Add estimated_cost column to agent_conversations.

Revision ID: 0028_estimated_cost
Revises: 0027_app_settings
Create Date: 2026-03-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0028_estimated_cost'
down_revision: Union[str, None] = '0027_app_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_conversations', sa.Column('estimated_cost', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_conversations', 'estimated_cost')
