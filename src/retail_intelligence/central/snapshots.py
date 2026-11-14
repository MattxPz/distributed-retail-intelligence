"""Initial inventory projection; HTTP transport remains a later stage."""

import hashlib
import json

from sqlalchemy import Engine, select

from retail_intelligence.central.models import metadata
from retail_intelligence.shared.events import InitialSnapshot


def apply_initial_snapshot(engine: Engine, snapshot: InitialSnapshot) -> bool:
    if engine.dialect.name != "postgresql":
        raise ValueError("Central projection requires PostgreSQL")
    tables = metadata.tables
    cursors, events = tables["sync_cursors"], tables["processed_events"]
    digest = hashlib.sha256(
        json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with engine.begin() as connection:
        cursor = (
            connection.execute(
                select(cursors).where(cursors.c.branch_id == snapshot.branch_id).with_for_update()
            )
            .mappings()
            .one()
        )
        previous = (
            connection.execute(select(events).where(events.c.id == str(snapshot.id)))
            .mappings()
            .first()
        )
        if previous is not None:
            if previous["payload_hash"] != digest:
                raise ValueError("Event identity conflict")
            return False
        if cursor["last_sequence"] != 0 or cursor["snapshot_version"] != 0:
            raise ValueError("Initial snapshot cannot replace an existing projection")
        connection.execute(
            tables["projected_stock"].insert(),
            [
                {
                    "branch_id": snapshot.branch_id,
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                }
                for item in snapshot.items
            ],
        )
        connection.execute(
            events.insert().values(
                id=str(snapshot.id), branch_id=snapshot.branch_id, sequence=1, payload_hash=digest
            )
        )
        connection.execute(
            cursors.update()
            .where(cursors.c.branch_id == snapshot.branch_id)
            .values(
                last_sequence=1,
                snapshot_version=1,
            )
        )
    return True
