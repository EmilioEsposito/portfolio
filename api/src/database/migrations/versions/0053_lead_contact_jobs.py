"""Quo Lead contacts: one job per person to create or fill in (``lead_contacts.py``).

``lead_contact_jobs`` holds a person's phone, name and email only until the job
is worked; ``key`` (the booking or Zillow thread, with a digest of the number)
keeps one number from being queued twice for the same booking or thread.
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_lead_contact_jobs"
down_revision = "0052_remove_booking_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_contact_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("attendee_id", sa.Integer(), nullable=True),
        sa.Column("zillow_thread_id", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("contact_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed')", name=op.f("ck_lead_contact_jobs_status")
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN "
            "('created', 'updated', 'unchanged', 'left_alone', 'skipped')",
            name=op.f("ck_lead_contact_jobs_result"),
        ),
        sa.ForeignKeyConstraint(
            ["attendee_id"],
            ["showing_attendees.id"],
            name=op.f("fk_lead_contact_jobs_attendee_id_showing_attendees"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead_contact_jobs")),
        sa.UniqueConstraint("key", name=op.f("uq_lead_contact_jobs_key")),
    )
    op.create_index(op.f("ix_lead_contact_jobs_attendee_id"), "lead_contact_jobs", ["attendee_id"])
    op.create_index(
        "ix_lead_contact_jobs_pending_next_attempt_at",
        "lead_contact_jobs",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_lead_contact_jobs_pending_next_attempt_at", table_name="lead_contact_jobs")
    op.drop_index(op.f("ix_lead_contact_jobs_attendee_id"), table_name="lead_contact_jobs")
    op.drop_table("lead_contact_jobs")
