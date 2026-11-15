"""Local atomic sale processing."""

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import OperationalError

from retail_intelligence.branch.models import metadata


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(strict=True, gt=0)
    quantity: int = Field(strict=True, gt=0)


class SaleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[Line] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_products(self) -> "SaleInput":
        if len({line.product_id for line in self.lines}) != len(self.lines):
            raise ValueError("Duplicate product")
        return self


def load_sale(connection: Connection, sale_id: str) -> dict[str, Any]:
    tables = metadata.tables
    sale = (
        connection.execute(select(tables["sales"]).where(tables["sales"].c.id == sale_id))
        .mappings()
        .one()
    )
    lines = (
        connection.execute(
            select(tables["sale_lines"])
            .where(tables["sale_lines"].c.sale_id == sale_id)
            .order_by(tables["sale_lines"].c.line_no)
        )
        .mappings()
        .all()
    )
    result = dict(sale)
    occurred_at = result["occurred_at"]
    if occurred_at.tzinfo is None:
        result["occurred_at"] = occurred_at.replace(tzinfo=UTC)
    result["lines"] = [dict(line) for line in lines]
    result["total_cents"] = sum(line["quantity"] * line["unit_price_cents"] for line in lines)
    return result


def create_sale(engine: Engine, branch_id: int, key: str, data: SaleInput) -> dict[str, Any]:
    lines = sorted(data.lines, key=lambda line: line.product_id)
    digest = hashlib.sha256(
        json.dumps(
            [line.model_dump() for line in lines], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    tables = metadata.tables
    for attempt in range(3):
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                previous = (
                    connection.execute(
                        select(tables["idempotency_keys"]).where(
                            tables["idempotency_keys"].c.branch_id == branch_id,
                            tables["idempotency_keys"].c.key == key,
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous:
                    if previous["request_hash"] != digest:
                        raise HTTPException(409, "Idempotency key reused with different content")
                    result = load_sale(connection, previous["sale_id"])
                    connection.commit()
                    return result
                products = {}
                for line in lines:
                    product = (
                        connection.execute(
                            select(tables["products"]).where(
                                tables["products"].c.id == line.product_id
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if product is None:
                        raise HTTPException(404, "Product not found")
                    products[line.product_id] = product
                for line in lines:
                    inventory = tables["inventory"]
                    changed = connection.execute(
                        inventory.update()
                        .where(
                            inventory.c.branch_id == branch_id,
                            inventory.c.product_id == line.product_id,
                            inventory.c.quantity >= line.quantity,
                        )
                        .values(quantity=inventory.c.quantity - line.quantity)
                    )
                    if changed.rowcount != 1:
                        raise HTTPException(409, "Insufficient stock")
                now = datetime.now(UTC)
                sale_id = str(uuid4())
                connection.execute(
                    tables["sales"]
                    .insert()
                    .values(
                        id=sale_id,
                        branch_id=branch_id,
                        occurred_at=now,
                        business_date=now.astimezone(ZoneInfo("America/Guayaquil")).date(),
                    )
                )
                rows = [
                    {
                        "sale_id": sale_id,
                        "line_no": i,
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                        "unit_price_cents": products[line.product_id]["price_cents"],
                    }
                    for i, line in enumerate(lines, 1)
                ]
                connection.execute(tables["sale_lines"].insert(), rows)
                state = tables["local_state"]
                sequence = connection.scalar(
                    select(state.c.next_sequence).where(state.c.branch_id == branch_id)
                )
                if sequence is None:
                    raise RuntimeError("Missing branch sequence state")
                connection.execute(
                    state.update()
                    .where(state.c.branch_id == branch_id)
                    .values(next_sequence=sequence + 1)
                )
                event_id = str(uuid4())
                payload = {
                    "id": event_id,
                    "branch_id": branch_id,
                    "sequence": sequence,
                    "schema_version": 1,
                    "event_type": "sale.created",
                    "occurred_at": now.isoformat(),
                    "sale_id": sale_id,
                    "business_date": now.astimezone(ZoneInfo("America/Guayaquil"))
                    .date()
                    .isoformat(),
                    "lines": rows,
                    "total_cents": sum(row["quantity"] * row["unit_price_cents"] for row in rows),
                }
                connection.execute(
                    tables["outbox"]
                    .insert()
                    .values(
                        id=event_id,
                        branch_id=branch_id,
                        sequence=sequence,
                        schema_version=1,
                        event_type="sale.created",
                        occurred_at=now,
                        payload=payload,
                    )
                )
                connection.execute(
                    tables["idempotency_keys"]
                    .insert()
                    .values(branch_id=branch_id, key=key, request_hash=digest, sale_id=sale_id)
                )
                result = load_sale(connection, sale_id)
                connection.commit()
                return result
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", None)
            if code is None or (code & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            if attempt == 2:
                raise HTTPException(503, "Database busy") from error
            time.sleep(0.05 * (attempt + 1))

    raise RuntimeError("Retry loop exited unexpectedly")
