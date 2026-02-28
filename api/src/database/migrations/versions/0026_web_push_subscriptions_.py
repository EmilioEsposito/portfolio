"""

Revision ID: 0026_web_push_subscriptions
Revises: 0025_sms_search_indexes
Create Date: 2026-02-28 10:00:17.845904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0026_web_push_subscriptions'
down_revision: Union[str, None] = '0025_sms_search_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('web_push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('clerk_user_id', sa.String(), nullable=False),
    sa.Column('endpoint', sa.String(), nullable=False),
    sa.Column('p256dh', sa.String(), nullable=False),
    sa.Column('auth', sa.String(), nullable=False),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_web_push_subscriptions')),
    sa.UniqueConstraint('endpoint', name=op.f('uq_web_push_subscriptions_endpoint'))
    )
    op.create_index(op.f('ix_web_push_subscriptions_clerk_user_id'), 'web_push_subscriptions', ['clerk_user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_web_push_subscriptions_clerk_user_id'), table_name='web_push_subscriptions')
    op.drop_table('web_push_subscriptions')
