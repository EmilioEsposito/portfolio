"""Remove the unused Google token cache, only when it is empty."""

from alembic import op
import sqlalchemy as sa

revision = "0041_drop_oauth_credentials"
down_revision = "0040_rental_listing_zillow_urls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check every target at execution time, including newly copied PR databases.
    # Hold the lock through DROP so a writer cannot race the emptiness check.
    # Keep the guard in SQL so offline-generated migrations are equally safe.
    op.execute("""
        DO $$
        BEGIN
            LOCK TABLE oauth_credentials IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM oauth_credentials) THEN
                RAISE EXCEPTION 'Refusing to drop nonempty oauth_credentials; preserve token data and investigate';
            END IF;
            RAISE NOTICE 'oauth_credentials verified empty under exclusive lock; dropping table';
            DROP TABLE oauth_credentials;
        END
        $$;
    """)


def downgrade() -> None:
    # Restore the empty schema as it stood after 0007 (naive expires_at).
    op.create_table(
        "oauth_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_user_id", sa.String(), nullable=False),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_type", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_credentials")),
        sa.UniqueConstraint("user_id", "provider", name="uix_user_provider"),
    )
    op.create_index(op.f("ix_oauth_credentials_provider"), "oauth_credentials", ["provider"])
    op.create_index(op.f("ix_oauth_credentials_user_id"), "oauth_credentials", ["user_id"])
