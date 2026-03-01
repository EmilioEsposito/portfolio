"""

Revision ID: 0027_app_settings
Revises: 0026_web_push_subscriptions
Create Date: 2026-02-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '0027_app_settings'
down_revision: Union[str, None] = '0026_web_push_subscriptions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('app_settings',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_app_settings')),
    )
    # Seed default: triggers enabled
    op.execute(
        "INSERT INTO app_settings (key, value) VALUES ('triggers_enabled', 'true'::jsonb) ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table('app_settings')
