"""M2: hip transfers (push to the HIU and status notification, per ABDM transaction)

Revision ID: d6f2b8e04a15
Revises: c5e1a7d93f04
Create Date: 2026-11-04 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6f2b8e04a15'
down_revision = 'c5e1a7d93f04'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hip_transfers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_client_id', sa.Integer(), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('consent_id', sa.String(length=64), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('data_push_url', sa.Text(), nullable=True),
    sa.Column('key_expiry', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('care_contexts', sa.JSON(), nullable=True),
    sa.Column('pending_notify', sa.JSON(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('api_client_id', 'transaction_id', name='uq_hip_transfer_client_txn')
    )
    with op.batch_alter_table('hip_transfers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hip_transfers_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_transfers_consent_id'), ['consent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_transfers_hip_id'), ['hip_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_transfers_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_transfers_transaction_id'), ['transaction_id'], unique=False)


def downgrade():
    op.drop_table('hip_transfers')
