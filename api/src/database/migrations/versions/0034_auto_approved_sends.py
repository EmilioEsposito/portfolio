"""Idempotency ledger for sends the agent makes without human approval."""
import sqlalchemy as sa
from alembic import op

revision = '0034_auto_approved_sends'
down_revision = '0033_zillow_email_jobs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'auto_approved_sends',
        sa.Column('idempotency_key', sa.String(), primary_key=True),
        sa.Column('tool_name', sa.String(), nullable=False),
        sa.Column('conversation_id', sa.String()),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table('auto_approved_sends')
