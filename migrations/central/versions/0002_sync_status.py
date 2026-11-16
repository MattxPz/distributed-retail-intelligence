"""Record receipt time for sync visibility."""

import sqlalchemy as sa
from alembic import op

revision = "central_0002"
down_revision = "central_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sync_cursors", sa.Column("last_synced_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("sync_cursors", "last_synced_at")
