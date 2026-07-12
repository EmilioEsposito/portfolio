"""Bump Sernia AI reasoning effort to "max" for the gpt-5.6-luna model_config.

GPT-5.6 added a "max" reasoning-effort tier above "xhigh" (even more
exploration/verification). Migration 0030 moved existing rows onto
gpt-5.6-luna / xhigh; this bumps that active row to the new top tier.

Only the gpt-5.6-luna row is touched — deliberate Anthropic selections
(sonnet-4-6 / opus-4-7) keep their effort (and "max" is OpenAI-only anyway).

Revision ID: 0031_sernia_effort_max
Revises: 0030_sernia_model_gpt56_luna
Create Date: 2026-07-12 00:45:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0031_sernia_effort_max'
down_revision: Union[str, None] = '0030_sernia_model_gpt56_luna'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Set the gpt-5.6-luna model_config row to max effort. Idempotent: re-running
    # is a no-op once the row already reads "max".
    op.execute(
        """
        UPDATE app_settings
        SET value = jsonb_set(value, '{thinking_effort}', '"max"')
        WHERE key = 'model_config'
          AND value->>'model_key' = 'gpt-5.6-luna'
          AND value->>'thinking_effort' IS DISTINCT FROM 'max'
        """
    )


def downgrade() -> None:
    # Revert to the prior effort established by migration 0030.
    op.execute(
        """
        UPDATE app_settings
        SET value = jsonb_set(value, '{thinking_effort}', '"xhigh"')
        WHERE key = 'model_config'
          AND value->>'model_key' = 'gpt-5.6-luna'
          AND value->>'thinking_effort' = 'max'
        """
    )
