"""Drop pending_email_approvals table

Now using DBOS recv/send for workflow state instead of custom table.

Revision ID: 0021_drop_pending_approvals
Revises: 0020_pending_approvals
Create Date: 2025-12-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0021_drop_pending_approvals'
down_revision: Union[str, None] = '0020_pending_approvals'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the custom table - DBOS now handles workflow state
    op.drop_table('pending_email_approvals')


def downgrade() -> None:
    # Recreate the table if rolling back
    op.create_table('pending_email_approvals',
        sa.Column('workflow_id', sa.String(length=36), nullable=False),
        sa.Column('tool_call_id', sa.String(length=255), nullable=False),
        sa.Column('message_history', sa.Text(), nullable=False),
        sa.Column('email_to', sa.String(length=255), nullable=False),
        sa.Column('email_subject', sa.String(length=500), nullable=False),
        sa.Column('email_body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('workflow_id', name=op.f('pk_pending_email_approvals'))
    )
