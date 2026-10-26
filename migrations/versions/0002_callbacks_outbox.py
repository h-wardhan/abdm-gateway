"""callbacks: hip registrations, callback events, outbox jobs

Revision ID: a7c3d9e15b02
Revises: fe1c9fac0150
Create Date: 2026-10-22 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3d9e15b02'
down_revision = 'fe1c9fac0150'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('hip_registrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_client_id', sa.Integer(), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('callback_url', sa.String(length=500), nullable=False),
    sa.Column('webhook_secret_enc', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('hip_registrations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hip_registrations_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hip_registrations_hip_id'), ['hip_id'], unique=True)

    op.create_table('callback_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('hip_id', sa.String(length=64), nullable=True),
    sa.Column('api_client_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('auth_result', sa.String(length=120), nullable=True),
    sa.Column('body_enc', sa.Text(), nullable=False),
    sa.Column('headers', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('request_id', 'path', name='uq_callback_request_path')
    )
    with op.batch_alter_table('callback_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_callback_events_api_client_id'), ['api_client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_callback_events_hip_id'), ['hip_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_callback_events_status'), ['status'], unique=False)

    op.create_table('outbox_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_status', sa.Integer(), nullable=True),
    sa.Column('payload_enc', sa.Text(), nullable=True),
    sa.Column('callback_event_id', sa.Integer(), nullable=True),
    sa.Column('api_client_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_client_id'], ['api_clients.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['callback_event_id'], ['callback_events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outbox_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outbox_jobs_callback_event_id'), ['callback_event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_outbox_jobs_next_attempt_at'), ['next_attempt_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_outbox_jobs_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('outbox_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outbox_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_outbox_jobs_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_outbox_jobs_callback_event_id'))
    op.drop_table('outbox_jobs')

    with op.batch_alter_table('callback_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_callback_events_status'))
        batch_op.drop_index(batch_op.f('ix_callback_events_hip_id'))
        batch_op.drop_index(batch_op.f('ix_callback_events_api_client_id'))
    op.drop_table('callback_events')

    with op.batch_alter_table('hip_registrations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hip_registrations_hip_id'))
        batch_op.drop_index(batch_op.f('ix_hip_registrations_api_client_id'))
    op.drop_table('hip_registrations')
