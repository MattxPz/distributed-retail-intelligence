"""Persist local demo controls and publisher state."""

import sqlalchemy as sa
from alembic import op

revision = "branch_0002"
down_revision = "branch_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sync_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.String(500)),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("id = 1", name="ck_sync_control_id"),
    )
    op.execute("INSERT INTO sync_control (id, paused, blocked) VALUES (1, false, false)")


def downgrade():
    op.drop_table("sync_control")
