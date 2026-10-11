"""Durable emergency escalation outbox; deliberately no historical backfill."""
from alembic import op
import sqlalchemy as sa

revision = '0032_escalation_outbox'
down_revision = '0031_sernia_effort_max'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('escalation_requests',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('source_key', sa.String(), nullable=False, unique=True),
        sa.Column('event_payload', sa.JSON()), sa.Column('message_text', sa.String()),
        sa.Column('state', sa.String(), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String()), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.String()))
    op.create_index('ix_escalation_requests_next_attempt_at', 'escalation_requests', ['next_attempt_at'])
    op.create_table('escalation_deliveries',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('request_id', sa.String(64), sa.ForeignKey('escalation_requests.id'), nullable=False),
        sa.Column('recipient_key', sa.String(), nullable=False), sa.Column('recipient', sa.String()),
        sa.Column('state', sa.String(), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_token', sa.String()), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('execution_sid', sa.String()), sa.Column('conflict_sid', sa.String()),
        sa.Column('last_error', sa.String()))
    op.create_index('ix_escalation_deliveries_request_id', 'escalation_deliveries', ['request_id'])
    op.create_index('ix_escalation_deliveries_next_attempt_at', 'escalation_deliveries', ['next_attempt_at'])
    op.create_table('escalation_attempts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('delivery_id', sa.String(64), sa.ForeignKey('escalation_deliveries.id'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('outcome', sa.String(), nullable=False), sa.Column('http_status', sa.Integer()),
        sa.Column('provider_code', sa.Integer()), sa.Column('execution_sid', sa.String()))
    op.create_index('ix_escalation_attempts_delivery_id', 'escalation_attempts', ['delivery_id'])


def downgrade() -> None:
    op.drop_table('escalation_attempts')
    op.drop_table('escalation_deliveries')
    op.drop_table('escalation_requests')
