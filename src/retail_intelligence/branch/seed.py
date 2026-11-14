from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, select

from retail_intelligence.branch.models import metadata
from retail_intelligence.shared.catalog import BRANCHES, INITIAL_QUANTITY, PRODUCTS
from retail_intelligence.shared.events import InitialSnapshot, StockItem


def seed(connection: Connection, branch_id: int) -> None:
    """Called only inside the bootstrap write transaction."""
    if branch_id not in (1, 2, 3):
        raise ValueError("Unknown branch")
    tables = metadata.tables
    state = connection.execute(select(tables["local_state"])).mappings().first()
    if state is not None:
        if state["branch_id"] != branch_id:
            raise ValueError("SQLite database belongs to a different branch")
        return
    connection.execute(tables["branches"].insert(), BRANCHES)
    connection.execute(tables["products"].insert(), PRODUCTS)
    connection.execute(
        tables["inventory"].insert(),
        [
            {"branch_id": branch_id, "product_id": product["id"], "quantity": INITIAL_QUANTITY}
            for product in PRODUCTS
        ],
    )
    snapshot = InitialSnapshot(
        id=uuid5(NAMESPACE_URL, f"retail/branch/{branch_id}/initial-snapshot/v1"),
        branch_id=branch_id,
        occurred_at=datetime.now(UTC),
        items=[
            StockItem(product_id=product_id, quantity=INITIAL_QUANTITY)
            for product_id in range(1, 11)
        ],
    )
    connection.execute(
        tables["outbox"]
        .insert()
        .values(
            id=str(snapshot.id),
            branch_id=branch_id,
            sequence=1,
            schema_version=1,
            event_type=snapshot.event_type,
            occurred_at=snapshot.occurred_at,
            payload=snapshot.model_dump(mode="json"),
        )
    )
    connection.execute(
        tables["local_state"]
        .insert()
        .values(
            id=1,
            branch_id=branch_id,
            next_sequence=2,
        )
    )
