"""M2: remember the HIU's key and the sender key used per transfer (so a push body with the wrong key material is corrected)

Revision ID: e7a3c9f15b26
Revises: d6f2b8e04a15
Create Date: 2026-11-12 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a3c9f15b26'
down_revision = 'd6f2b8e04a15'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('hip_transfers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hiu_public_key', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('hiu_nonce', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('sender_public_key', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('sender_nonce', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('hip_transfers', schema=None) as batch_op:
        batch_op.drop_column('sender_nonce')
        batch_op.drop_column('sender_public_key')
        batch_op.drop_column('hiu_nonce')
        batch_op.drop_column('hiu_public_key')
