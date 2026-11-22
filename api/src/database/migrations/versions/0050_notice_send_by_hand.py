"""Showing bookings: who sent a captured message by hand, and the test send mode.

``showing_notices.send_approved_by`` and ``send_approved_at``: the team member
who sent a captured message by hand, by Sernia email, and when.

``showing_notices.mode`` records the send mode a notice was delivered in. The
mode "off" is gone and "test" is new, so the column's check takes "test" too;
"off" stays for the rows that have it. The settings row needs no change: an old
one reads correctly (``showing_bookings/settings.py``).
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_notice_send_by_hand"
down_revision = "0049_lead_reply_uncertain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("showing_notices", sa.Column("send_approved_by", sa.String(), nullable=True))
    op.add_column(
        "showing_notices",
        sa.Column("send_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(op.f("ck_showing_notices_mode"), "showing_notices", type_="check")
    op.create_check_constraint(
        op.f("ck_showing_notices_mode"),
        "showing_notices",
        "mode IS NULL OR mode IN ('off', 'test', 'capture', 'live')",
    )


def downgrade() -> None:
    # Test mode was the live mode with test sends on.
    op.execute("UPDATE showing_notices SET mode = 'live' WHERE mode = 'test'")
    op.drop_constraint(op.f("ck_showing_notices_mode"), "showing_notices", type_="check")
    op.create_check_constraint(
        op.f("ck_showing_notices_mode"),
        "showing_notices",
        "mode IS NULL OR mode IN ('off', 'capture', 'live')",
    )
    op.drop_column("showing_notices", "send_approved_at")
    op.drop_column("showing_notices", "send_approved_by")
