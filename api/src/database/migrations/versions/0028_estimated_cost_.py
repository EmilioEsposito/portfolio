"""Add cost tracking and run_count to agent_conversations.

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
    op.add_column('agent_conversations', sa.Column('cost_last_run', sa.Float(), nullable=True))
    op.add_column('agent_conversations', sa.Column('cost_total', sa.Float(), nullable=False, server_default='0'))
    op.add_column('agent_conversations', sa.Column('run_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('agent_conversations', 'run_count')
    op.drop_column('agent_conversations', 'cost_total')
    op.drop_column('agent_conversations', 'cost_last_run')
