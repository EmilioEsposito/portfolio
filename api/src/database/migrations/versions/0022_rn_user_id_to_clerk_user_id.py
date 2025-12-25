"""
Rename user_id to clerk_user_id in agent_conversations table.

This aligns with the convention used in the User model (user/models.py)
where clerk_user_id is the Clerk user ID string.

Revision ID: 0022_rename_user_id_to_clerk_user_id_in_agent_conversations
Revises: 0021_add_user_email
Create Date: 2025-12-25 13:54:50.251588

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0022_rn_user_id_to_clerk_user_id'
down_revision: Union[str, None] = '0021_add_user_email'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename column from user_id to clerk_user_id
    op.alter_column(
        'agent_conversations',
        'user_id',
        new_column_name='clerk_user_id'
    )

    # Rename the index as well
    op.drop_index('ix_agent_conversations_user_id', table_name='agent_conversations')
    op.create_index(
        'ix_agent_conversations_clerk_user_id',
        'agent_conversations',
        ['clerk_user_id'],
        unique=False
    )


def downgrade() -> None:
    # Rename column back from clerk_user_id to user_id
    op.alter_column(
        'agent_conversations',
        'clerk_user_id',
        new_column_name='user_id'
    )

    # Rename the index back
    op.drop_index('ix_agent_conversations_clerk_user_id', table_name='agent_conversations')
    op.create_index(
        'ix_agent_conversations_user_id',
        'agent_conversations',
        ['user_id'],
        unique=False
    )
