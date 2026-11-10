"""Durable queue of inbound Zillow emails; deliberately no historical backfill."""
import sqlalchemy as sa
from alembic import op

revision = '0033_zillow_email_jobs'
down_revision = '0032_escalation_outbox'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'zillow_email_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('message_id', sa.String(), nullable=False, unique=True),
        sa.Column('thread_id', sa.String(), nullable=False),
        sa.Column('subject', sa.String()),
        sa.Column('from_address', sa.String()),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.String()),
        sa.Column('conversation_id', sa.String()),
    )
    op.create_index('ix_zillow_email_jobs_thread_id', 'zillow_email_jobs', ['thread_id'])
    op.create_index('ix_zillow_email_jobs_state', 'zillow_email_jobs', ['state'])
    op.create_index('ix_zillow_email_jobs_next_attempt_at', 'zillow_email_jobs', ['next_attempt_at'])


def downgrade() -> None:
    op.drop_table('zillow_email_jobs')
