"""M2: link tokens kept by the gateway, and the hold of a consent notice until its health-information request

Revision ID: a9c5e3b72d48
Revises: f8b4d2a61c37
Create Date: 2026-11-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9c5e3b72d48'
down_revision = 'f8b4d2a61c37'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hip_link_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=False),
    sa.Column('abha_address', sa.String(length=255), nullable=False),
    sa.Column('token_enc', sa.Text(), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('callback_event_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['callback_event_id'], ['callback_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hip_id', 'abha_address', name='uq_hip_link_token')
    )
    with op.batch_alter_table('hip_link_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hip_link_tokens_hip_id'), ['hip_id'], unique=False)

    with op.batch_alter_table('outbox_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hold_consent_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_outbox_jobs_hold_consent_id'), ['hold_consent_id'], unique=False)


def downgrade():
    with op.batch_alter_table('outbox_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outbox_jobs_hold_consent_id'))
        batch_op.drop_column('hold_consent_id')
    op.drop_table('hip_link_tokens')
