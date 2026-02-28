"""Add indexes on open_phone_events for SMS history search

Revision ID: 0025_sms_search_indexes
Revises: 0024_sernia_columns
Create Date: 2026-02-28

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0025_sms_search_indexes'
down_revision: Union[str, None] = '0024_sernia_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable trigram extension for ILIKE performance (available on Neon Postgres)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Trigram index for keyword search on message_text
    op.execute(
        "CREATE INDEX idx_ope_message_text_trgm ON open_phone_events "
        "USING GIN (message_text gin_trgm_ops)"
    )

    # Composite index for context window lookups (conversation + time ordering)
    op.create_index(
        "idx_ope_conv_ts",
        "open_phone_events",
        ["conversation_id", "event_timestamp"],
    )

    # Phone number indexes for contact-based filtering
    op.create_index("idx_ope_from_number", "open_phone_events", ["from_number"])
    op.create_index("idx_ope_to_number", "open_phone_events", ["to_number"])


def downgrade() -> None:
    op.drop_index("idx_ope_to_number", table_name="open_phone_events")
    op.drop_index("idx_ope_from_number", table_name="open_phone_events")
    op.drop_index("idx_ope_conv_ts", table_name="open_phone_events")
    op.execute("DROP INDEX IF EXISTS idx_ope_message_text_trgm")
    # Don't drop pg_trgm extension — other things may use it
