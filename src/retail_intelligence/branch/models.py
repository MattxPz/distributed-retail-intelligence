"""Database schemas for the branch and central persistence boundaries."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()


def common_tables(metadata: MetaData) -> None:
    Table(
        "branches",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(100), nullable=False, unique=True),
    )
    Table(
        "products",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("sku", String(64), nullable=False, unique=True),
        Column("name", String(200), nullable=False),
        Column("price_cents", Integer, nullable=False),
        CheckConstraint(
            "price_cents >= 0 AND price_cents = CAST(price_cents AS INTEGER)",
            name="ck_product_price",
        ),
    )


def sales_tables(metadata: MetaData) -> None:
    Table(
        "sales",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("business_date", Date, nullable=False),
        UniqueConstraint("branch_id", "id", name="uq_sale_branch_id"),
    )
    Table(
        "sale_lines",
        metadata,
        Column("sale_id", String(36), ForeignKey("sales.id"), primary_key=True),
        Column("line_no", Integer, primary_key=True),
        Column("product_id", Integer, ForeignKey("products.id"), nullable=False),
        Column("quantity", Integer, nullable=False),
        Column("unit_price_cents", Integer, nullable=False),
        CheckConstraint(
            "line_no > 0 AND quantity > 0 AND quantity = CAST(quantity AS INTEGER) "
            "AND unit_price_cents >= 0 "
            "AND unit_price_cents = CAST(unit_price_cents AS INTEGER)",
            name="ck_line_values",
        ),
    )
    Index(
        "ix_sales_branch_date",
        metadata.tables["sales"].c.branch_id,
        metadata.tables["sales"].c.business_date,
    )


common_tables(metadata)
sales_tables(metadata)
Table(
    "inventory",
    metadata,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("product_id", Integer, ForeignKey("products.id"), primary_key=True),
    Column("quantity", Integer, nullable=False),
    CheckConstraint(
        "quantity >= 0 AND quantity = CAST(quantity AS INTEGER)", name="ck_inventory_quantity"
    ),
)
Table(
    "idempotency_keys",
    metadata,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("key", String(200), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("sale_id", String(36), nullable=False),
    ForeignKeyConstraint(["branch_id", "sale_id"], ["sales.branch_id", "sales.id"]),
)
Table(
    "outbox",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("event_type", String(80), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    UniqueConstraint("branch_id", "sequence", name="uq_outbox_sequence"),
    CheckConstraint("sequence > 0 AND schema_version > 0", name="ck_outbox_sequence"),
)
Index(
    "ix_outbox_pending",
    metadata.tables["outbox"].c.sent_at,
    metadata.tables["outbox"].c.sequence,
)


Index("ix_lines_product", metadata.tables["sale_lines"].c.product_id)

Table(
    "local_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False, unique=True),
    Column("next_sequence", Integer, nullable=False),
    CheckConstraint("id = 1 AND next_sequence >= 2", name="ck_local_state"),
)

Table(
    "sync_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("paused", Boolean, nullable=False, server_default="false"),
    Column("last_error", String(500)),
    Column("blocked", Boolean, nullable=False, server_default="false"),
    CheckConstraint("id = 1", name="ck_sync_control_id"),
)
