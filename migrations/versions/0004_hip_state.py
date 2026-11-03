"""M2: hip replies (duplicate guard) and hip consents

Revision ID: c5e1a7d93f04
Revises: b4d8e2f61c03
Create Date: 2026-11-03 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5e1a7d93f04'
down_revision = 'b4d8e2f61c03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hip_replies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_client_id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('request_id', 'kind', name='uq_hip_reply_request_kind')
    )
    with op.batch_alter_table('hip_replies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hip_replies_api_client_id'), ['api_client_id'], unique=False)

    op.create_table('hip_consents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consent_id', sa.String(length=64), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=True),
    sa.Column('api_client_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('hi_types', sa.JSON(), nullable=True),
    sa.Column('care_contexts', sa.JSON(), nullable=True),
    sa.Column('date_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('date_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('erase_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('hip_consents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hip_consents_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_consents_consent_id'), ['consent_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_hip_consents_hip_id'), ['hip_id'], unique=False)


def downgrade():
    op.drop_table('hip_consents')
    op.drop_table('hip_replies')
