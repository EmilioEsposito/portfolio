"""Showing bookings: the one alert to the team about a notice that failed or is stuck.

- ``showing_notices.alerted_at``: when the team was alerted about this notice.
  Set before the alert goes, so a notice is alerted about once at most.
- ``showing_notices.alert_reason``: ``failed`` (failed, or may have gone out,
  and never tried again) or ``stuck`` (not gone out ten minutes after it was
  due).

Reminders and the host's texts need no column: they are notices like any other.
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_showing_notice_alerts"
down_revision = "0045_showing_resident_attention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "showing_notices", sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("showing_notices", sa.Column("alert_reason", sa.String(), nullable=True))
    op.create_check_constraint(
        op.f("ck_showing_notices_alert_reason"),
        "showing_notices",
        "alert_reason IN ('failed', 'stuck')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_showing_notices_alert_reason"), "showing_notices", type_="check")
    op.drop_column("showing_notices", "alert_reason")
    op.drop_column("showing_notices", "alerted_at")
