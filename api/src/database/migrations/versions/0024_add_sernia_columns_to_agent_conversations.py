"""Add modality, contact_identifier, estimated_tokens to agent_conversations

Revision ID: 0024_sernia_columns
Revises: 0023_index_optimize
Create Date: 2026-02-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0024_sernia_columns'
down_revision: Union[str, None] = '0023_index_optimize'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agent_conversations',
        sa.Column('modality', sa.String(), nullable=True, server_default='web_chat'),
    )
    op.add_column(
        'agent_conversations',
        sa.Column('contact_identifier', sa.String(), nullable=True),
    )
    op.add_column(
        'agent_conversations',
        sa.Column('estimated_tokens', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_agent_conv_modality', 'agent_conversations', ['modality'])
    op.create_index('ix_agent_conv_contact', 'agent_conversations', ['contact_identifier'])


def downgrade() -> None:
    op.drop_index('ix_agent_conv_contact', table_name='agent_conversations')
    op.drop_index('ix_agent_conv_modality', table_name='agent_conversations')
    op.drop_column('agent_conversations', 'estimated_tokens')
    op.drop_column('agent_conversations', 'contact_identifier')
    op.drop_column('agent_conversations', 'modality')
