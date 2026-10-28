"""client default webhook, auto registered hips

Revision ID: b4d8e2f61c03
Revises: a7c3d9e15b02
Create Date: 2026-10-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4d8e2f61c03'
down_revision = 'a7c3d9e15b02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('api_clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('callback_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('webhook_secret_enc', sa.Text(), nullable=True))

    with op.batch_alter_table('hip_registrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auto_registered', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('hip_registrations', schema=None) as batch_op:
        batch_op.drop_column('auto_registered')

    with op.batch_alter_table('api_clients', schema=None) as batch_op:
        batch_op.drop_column('webhook_secret_enc')
        batch_op.drop_column('callback_url')
