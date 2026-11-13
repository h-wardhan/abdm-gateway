"""M3: hiu consents and hiu transfers (what the facility asked as HIU, from ABDM's callbacks)

Revision ID: f8b4d2a61c37
Revises: e7a3c9f15b26
Create Date: 2026-11-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8b4d2a61c37'
down_revision = 'e7a3c9f15b26'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hiu_consents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_client_id', sa.Integer(), nullable=False),
    sa.Column('hiu_id', sa.String(length=64), nullable=False),
    sa.Column('consent_request_id', sa.String(length=64), nullable=True),
    sa.Column('consent_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=True),
    sa.Column('hi_types', sa.JSON(), nullable=True),
    sa.Column('date_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('date_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('erase_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('hiu_consents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hiu_consents_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_consents_hiu_id'), ['hiu_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_consents_consent_request_id'), ['consent_request_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_consents_consent_id'), ['consent_id'], unique=True)

    op.create_table('hiu_transfers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_client_id', sa.Integer(), nullable=False),
    sa.Column('hiu_id', sa.String(length=64), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('consent_id', sa.String(length=64), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('session_status', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('request_id')
    )
    with op.batch_alter_table('hiu_transfers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hiu_transfers_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_transfers_hiu_id'), ['hiu_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_transfers_consent_id'), ['consent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hiu_transfers_transaction_id'), ['transaction_id'], unique=False)


def downgrade():
    op.drop_table('hiu_transfers')
    op.drop_table('hiu_consents')
