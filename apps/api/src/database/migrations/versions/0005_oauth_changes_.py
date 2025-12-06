"""

Revision ID: 0005_oauth_changes
Revises: 0004_oath_table
Create Date: 2025-02-16 17:27:55.384347

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0005_oauth_changes'
down_revision: Union[str, None] = '0004_oath_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First create a temporary column for the array
    op.add_column('google_oauth_tokens',
        sa.Column('scopes_new', postgresql.ARRAY(sa.String()), nullable=True)
    )
    
    # Update the new column using jsonb_array_elements
    op.execute("""
        UPDATE google_oauth_tokens
        SET scopes_new = array(
            SELECT jsonb_array_elements_text(scopes::jsonb)
        )
    """)
    
    # Drop the old column and rename the new one
    op.drop_column('google_oauth_tokens', 'scopes')
    op.alter_column('google_oauth_tokens', 'scopes_new',
        new_column_name='scopes',
        nullable=False
    )
               
    # Drop the timestamp columns first
    op.drop_column('google_oauth_tokens', 'updated_at')
    op.drop_column('google_oauth_tokens', 'created_at')
    
    # Create a new column for the non-timezone expiry
    op.add_column('google_oauth_tokens', 
        sa.Column('expiry_new', sa.DateTime(), nullable=True)
    )
    
    # Copy data with timezone conversion
    op.execute("""
        UPDATE google_oauth_tokens 
        SET expiry_new = expiry AT TIME ZONE 'UTC'
    """)
    
    # Drop the old column and rename the new one
    op.drop_column('google_oauth_tokens', 'expiry')
    op.alter_column('google_oauth_tokens', 'expiry_new',
        new_column_name='expiry',
        nullable=False
    )
    
    # Update indexes
    op.drop_index('ix_google_oauth_tokens_user_id', table_name='google_oauth_tokens')
    op.create_unique_constraint(
        op.f('uq_google_oauth_tokens_user_id'), 
        'google_oauth_tokens', 
        ['user_id']
    )


def downgrade() -> None:
    # Add back the timestamp columns
    op.add_column('google_oauth_tokens', 
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), 
                 server_default=sa.text('now()'), 
                 nullable=False)
    )
    op.add_column('google_oauth_tokens', 
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), 
                 server_default=sa.text('now()'), 
                 nullable=False)
    )
    
    # Create a new column for the timezone-aware expiry
    op.add_column('google_oauth_tokens',
        sa.Column('expiry_tz', postgresql.TIMESTAMP(timezone=True), nullable=True)
    )
    
    # Copy data with timezone conversion
    op.execute("""
        UPDATE google_oauth_tokens 
        SET expiry_tz = expiry AT TIME ZONE 'UTC'
    """)
    
    # Drop the old column and rename the new one
    op.drop_column('google_oauth_tokens', 'expiry')
    op.alter_column('google_oauth_tokens', 'expiry_tz',
        new_column_name='expiry',
        nullable=False
    )
    
    # Convert ARRAY back to JSON - create temporary column first
    op.add_column('google_oauth_tokens',
        sa.Column('scopes_json', postgresql.JSON(), nullable=True)
    )
    
    # Convert array to JSON array
    op.execute("""
        UPDATE google_oauth_tokens
        SET scopes_json = to_json(scopes)
    """)
    
    # Drop the old column and rename the new one
    op.drop_column('google_oauth_tokens', 'scopes')
    op.alter_column('google_oauth_tokens', 'scopes_json',
        new_column_name='scopes',
        nullable=False
    )
               
    # Restore indexes
    op.drop_constraint('uq_google_oauth_tokens_user_id', 'google_oauth_tokens', type_='unique')
    op.create_index('ix_google_oauth_tokens_user_id', 'google_oauth_tokens', ['user_id'], unique=True)
