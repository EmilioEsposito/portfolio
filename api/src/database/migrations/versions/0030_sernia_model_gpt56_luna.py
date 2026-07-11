"""Upgrade Sernia AI model_config to gpt-5.6-luna at max (xhigh) effort.

The ``gpt-5.4`` model key was retired from ``sernia_ai.model_config.ModelKey``
in favor of ``gpt-5.6-luna``. Existing environments (production, and Neon PR
branches that inherit production rows) store ``model_config`` = ``gpt-5.4`` /
``medium``. This data migration moves those rows onto the new model at maximum
reasoning effort so the upgrade takes effect on deploy without a manual admin
edit.

Only rows currently on the retired ``gpt-5.4`` key are touched — deliberate
Anthropic selections (``sonnet-4-6`` / ``opus-4-7``) are left as-is. If the row
is missing entirely, it is inserted with the new values.

Revision ID: 0030_sernia_model_gpt56_luna
Revises: 0029_repair_cost_columns
Create Date: 2026-07-11 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0030_sernia_model_gpt56_luna'
down_revision: Union[str, None] = '0029_repair_cost_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Insert the row if absent; if present and still on the retired gpt-5.4 key,
    # move it to gpt-5.6-luna / xhigh. Rows deliberately set to an Anthropic
    # model are untouched (the DO UPDATE WHERE clause skips them).
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('model_config', '{"model_key": "gpt-5.6-luna", "thinking_effort": "xhigh"}'::jsonb)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value
            WHERE app_settings.value->>'model_key' = 'gpt-5.4'
        """
    )


def downgrade() -> None:
    # Revert rows we moved onto gpt-5.6-luna back to the prior gpt-5.4 / medium.
    op.execute(
        """
        UPDATE app_settings
        SET value = '{"model_key": "gpt-5.4", "thinking_effort": "medium"}'::jsonb
        WHERE key = 'model_config'
          AND value->>'model_key' = 'gpt-5.6-luna'
        """
    )
