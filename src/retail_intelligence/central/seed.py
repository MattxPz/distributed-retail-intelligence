from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from retail_intelligence.central.models import metadata
from retail_intelligence.shared.catalog import BRANCHES, PRODUCTS


def seed(connection: Connection) -> None:
    """Catalog only: projected stock is populated exclusively by events."""
    for name, rows in [("branches", BRANCHES), ("products", PRODUCTS)]:
        connection.execute(insert(metadata.tables[name]).values(rows).on_conflict_do_nothing())
    connection.execute(
        insert(metadata.tables["sync_cursors"])
        .values([{"branch_id": i, "last_sequence": 0, "snapshot_version": 0} for i in range(1, 4)])
        .on_conflict_do_nothing()
    )
