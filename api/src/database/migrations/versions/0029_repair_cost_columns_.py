"""Repair agent_conversations cost columns.

Migration 0028 was edited after it had already been applied to prod/dev,
so those databases have a stale 'estimated_cost' column instead of the
correct 'cost_last_run', 'cost_total', and 'run_count' columns.

This repair migration is idempotent: it uses IF EXISTS / IF NOT EXISTS
so it works on both fresh databases (where 0028 already created the
correct columns) and drifted databases (where the old column lingers).

Revision ID: 0029_repair_cost_columns
Revises: 0028_estimated_cost
Create Date: 2026-03-08 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0029_repair_cost_columns'
down_revision: Union[str, None] = '0028_estimated_cost'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the stale column left by the original (pre-edit) 0028
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS estimated_cost")

    # Add the correct columns (no-op on fresh databases where 0028 already added them)
    op.execute(
        "ALTER TABLE agent_conversations "
        "ADD COLUMN IF NOT EXISTS cost_last_run DOUBLE PRECISION"
    )
    op.execute(
        "ALTER TABLE agent_conversations "
        "ADD COLUMN IF NOT EXISTS cost_total DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE agent_conversations "
        "ADD COLUMN IF NOT EXISTS run_count INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS cost_last_run")
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS cost_total")
    op.execute("ALTER TABLE agent_conversations DROP COLUMN IF EXISTS run_count")
