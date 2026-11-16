"""Ordered, transactional event projection for PostgreSQL."""

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from retail_intelligence.central.models import metadata
from retail_intelligence.shared.events import InitialSnapshot

logger = logging.getLogger("uvicorn.error")


class EventError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable
        self.expected_sequence: int | None = None


def process_event(engine: Engine, event: dict[str, Any]) -> str:
    """Commit dedupe, stock, sale, and cursor together; duplicates are acknowledged."""
    try:
        event_id = str(UUID(event["id"]))
        branch = event["branch_id"]
        sequence = event["sequence"]
        if type(branch) is not int or branch not in (1, 2, 3):
            raise ValueError("invalid branch")
        if (
            type(sequence) is not int
            or sequence < 1
            or type(event["schema_version"]) is not int
            or event["schema_version"] != 1
        ):
            raise ValueError("invalid sequence or version")
        occurred = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
        if occurred.utcoffset() != UTC.utcoffset(occurred):
            raise ValueError("timestamp must be UTC")
        kind = event["event_type"]
        if kind == "inventory.initialized":
            snapshot = InitialSnapshot.model_validate(event)
            if sequence != 1:
                raise ValueError("snapshot must be first")
        elif kind == "sale.created":
            expected_fields = {
                "id",
                "branch_id",
                "sequence",
                "schema_version",
                "event_type",
                "occurred_at",
                "sale_id",
                "business_date",
                "lines",
                "total_cents",
            }
            if set(event) != expected_fields:
                raise ValueError("unsupported sale schema")
            sale_id = str(UUID(event["sale_id"]))
            business_date = date.fromisoformat(event["business_date"])
            if business_date != occurred.astimezone(ZoneInfo("America/Guayaquil")).date():
                raise ValueError("business date does not match event time")
            lines = event["lines"]
            if sequence < 2 or not isinstance(lines, list) or not lines:
                raise ValueError("invalid sale")
            products = set()
            for i, line in enumerate(lines, 1):
                if not isinstance(line, dict) or set(line) != {
                    "sale_id",
                    "line_no",
                    "product_id",
                    "quantity",
                    "unit_price_cents",
                }:
                    raise ValueError("unsupported sale line schema")
                if (
                    line["sale_id"] != sale_id
                    or type(line["line_no"]) is not int
                    or line["line_no"] != i
                ):
                    raise ValueError("invalid line")
                for field in ("product_id", "quantity", "unit_price_cents"):
                    if (
                        type(line[field]) is not int
                        or line[field] < (0 if field == "unit_price_cents" else 1)
                        or line[field] > 2_147_483_647
                    ):
                        raise ValueError("invalid line values")
                if line["product_id"] in products:
                    raise ValueError("duplicate product")
                products.add(line["product_id"])
            if type(event["total_cents"]) is not int or event["total_cents"] != sum(
                line["quantity"] * line["unit_price_cents"] for line in lines
            ):
                raise ValueError("total mismatch")
        else:
            raise ValueError("unsupported event type")
        digest = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        logger.warning(
            "Invalid event: id=%s branch=%s error=%s",
            event.get("id"),
            event.get("branch_id"),
            error,
        )
        raise EventError("invalid", str(error)) from error
    tables = metadata.tables
    cursor_table, processed = tables["sync_cursors"], tables["processed_events"]
    try:
        with engine.begin() as connection:
            cursor = (
                connection.execute(
                    select(cursor_table).where(cursor_table.c.branch_id == branch).with_for_update()
                )
                .mappings()
                .one()
            )
            inserted = connection.execute(
                insert(processed)
                .values(id=event_id, branch_id=branch, sequence=sequence, payload_hash=digest)
                .on_conflict_do_nothing()
                .returning(processed.c.id)
            ).scalar_one_or_none()
            if inserted is None:
                existing = (
                    connection.execute(select(processed).where(processed.c.id == event_id))
                    .mappings()
                    .first()
                )
                if (
                    existing is not None
                    and existing["branch_id"] == branch
                    and existing["sequence"] == sequence
                    and existing["payload_hash"] == digest
                ):
                    return "duplicate"
                logger.error(
                    "Event identity or sequence conflict: branch=%s sequence=%s event=%s",
                    branch,
                    sequence,
                    event_id,
                )
                raise EventError("conflict", "event identity or sequence conflict")
            if sequence != cursor["last_sequence"] + 1:
                error = EventError("gap", f"expected sequence {cursor['last_sequence'] + 1}", True)
                error.expected_sequence = cursor["last_sequence"] + 1
                raise error
            if kind == "inventory.initialized":
                if cursor["snapshot_version"] != 0:
                    raise EventError("conflict", "snapshot already initialized")
                connection.execute(
                    tables["projected_stock"].insert(),
                    [
                        {
                            "branch_id": branch,
                            "product_id": item.product_id,
                            "quantity": item.quantity,
                        }
                        for item in snapshot.items
                    ],
                )
                version = 1
            else:
                if cursor["snapshot_version"] != 1:
                    raise EventError("gap", "snapshot required", True)
                connection.execute(
                    tables["sales"]
                    .insert()
                    .values(
                        id=sale_id,
                        branch_id=branch,
                        occurred_at=occurred,
                        business_date=business_date,
                    )
                )
                connection.execute(tables["sale_lines"].insert(), lines)
                stock = tables["projected_stock"]
                for line in lines:
                    changed = connection.execute(
                        stock.update()
                        .where(
                            stock.c.branch_id == branch,
                            stock.c.product_id == line["product_id"],
                            stock.c.quantity >= line["quantity"],
                        )
                        .values(quantity=stock.c.quantity - line["quantity"])
                    )
                    if changed.rowcount != 1:
                        raise EventError("invalid", "stock projection mismatch")
                version = cursor["snapshot_version"]
            connection.execute(
                cursor_table.update()
                .where(cursor_table.c.branch_id == branch)
                .values(
                    last_sequence=sequence,
                    snapshot_version=version,
                    last_synced_at=datetime.now(UTC),
                )
            )
        return "accepted"
    except IntegrityError as error:
        logger.warning("Invalid projection branch=%s sequence=%s", branch, sequence)
        raise EventError("invalid", "projection constraint violated") from error
