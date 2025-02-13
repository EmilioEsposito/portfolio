"""

Revision ID: 0003_create_email_messages
Revises: 0002_create_example2_table
Create Date: 2025-02-13 14:59:27.126639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_create_email_messages'
down_revision: Union[str, None] = '0002_create_example2_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('email_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=False),
    sa.Column('subject', sa.String(), nullable=False),
    sa.Column('from_address', sa.String(), nullable=False),
    sa.Column('to_address', sa.String(), nullable=False),
    sa.Column('received_date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('body_text', sa.Text(), nullable=True),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('raw_payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_messages'))
    )
    op.create_index(op.f('ix_email_messages_message_id'), 'email_messages', ['message_id'], unique=True)
    op.create_index(op.f('ix_email_messages_thread_id'), 'email_messages', ['thread_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_email_messages_thread_id'), table_name='email_messages')
    op.drop_index(op.f('ix_email_messages_message_id'), table_name='email_messages')
    op.drop_table('email_messages')
    # ### end Alembic commands ###
