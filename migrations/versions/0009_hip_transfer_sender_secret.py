"""M2: the sender key the gateway chose for a transfer, kept sealed until the transfer is over

Revision ID: b3d7f1a94c52
Revises: a9c5e3b72d48
Create Date: 2026-11-17 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d7f1a94c52'
down_revision = 'a9c5e3b72d48'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('hip_transfers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sender_private_key_enc', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('hip_transfers', schema=None) as batch_op:
        batch_op.drop_column('sender_private_key_enc')
