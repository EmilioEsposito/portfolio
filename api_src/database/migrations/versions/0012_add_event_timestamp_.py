"""add event_timestamp

Revision ID: 0012_add_event_timestamp
Revises: 0011_open_phone_events
Create Date: 2024-03-30 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import datetime
import json

# revision identifiers, used by Alembic.
revision = '0012_add_event_timestamp'
down_revision = '0011_open_phone_events'
branch_labels = None
depends_on = None

def upgrade():
    # Add event_timestamp column
    op.add_column('open_phone_events', sa.Column('event_timestamp', sa.DateTime(timezone=True), nullable=True))
    
    # Backfill event_timestamp from event_data
    connection = op.get_bind()
    connection.execute(
        text("""
        UPDATE open_phone_events 
        SET event_timestamp = (event_data->>'createdAt')::timestamp with time zone
        WHERE event_data->>'createdAt' IS NOT NULL
        """)
    )

def downgrade():
    op.drop_column('open_phone_events', 'event_timestamp') 