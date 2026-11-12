"""Move Sernia AI model_config from gpt-5.6-luna to gpt-6-luna.

The live agent model is the DB-backed ``model_config`` app setting, so this data
migration is what switches production on deploy. Only the model changes: the
row's current ``thinking_effort`` is kept as-is.

Only rows currently on ``gpt-5.6-luna`` are touched. Deliberate selections of
another model (``sonnet-4-6`` / ``opus-4-7``) are left alone, and
``gpt-5.6-luna`` stays selectable in the settings UI. If the row is missing
entirely, it is inserted at ``gpt-6-luna`` / ``max`` to match ``seed_db.py``.

Revision ID: 0035_sernia_model_gpt6_luna
Revises: 0034_auto_approved_sends
Create Date: 2026-11-11 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_sernia_model_gpt6_luna"
down_revision: str | None = "0034_auto_approved_sends"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Insert the row if absent; otherwise swap only the model key on rows still
    # on gpt-5.6-luna, preserving their thinking_effort. Idempotent: a second
    # run matches nothing once the row reads gpt-6-luna.
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('model_config', '{"model_key": "gpt-6-luna", "thinking_effort": "max"}'::jsonb)
        ON CONFLICT (key) DO UPDATE
            SET value = jsonb_set(app_settings.value, '{model_key}', '"gpt-6-luna"')
            WHERE app_settings.value->>'model_key' = 'gpt-5.6-luna'
        """
    )


def downgrade() -> None:
    # Move gpt-6-luna rows back to gpt-5.6-luna, again keeping thinking_effort.
    op.execute(
        """
        UPDATE app_settings
        SET value = jsonb_set(value, '{model_key}', '"gpt-5.6-luna"')
        WHERE key = 'model_config'
          AND value->>'model_key' = 'gpt-6-luna'
        """
    )
