"""Database schemas for the branch and central persistence boundaries."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
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
    "projected_stock",
    metadata,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("product_id", Integer, ForeignKey("products.id"), primary_key=True),
    Column("quantity", Integer, nullable=False),
    CheckConstraint(
        "quantity >= 0 AND quantity = CAST(quantity AS INTEGER)", name="ck_projected_stock_quantity"
    ),
)
Table(
    "processed_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("branch_id", Integer, ForeignKey("branches.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    UniqueConstraint("branch_id", "sequence", name="uq_processed_sequence"),
    CheckConstraint("sequence > 0", name="ck_processed_sequence"),
)
Table(
    "sync_cursors",
    metadata,
    Column("branch_id", Integer, ForeignKey("branches.id"), primary_key=True),
    Column("last_sequence", Integer, nullable=False),
    Column("snapshot_version", Integer, nullable=False),
    CheckConstraint("last_sequence >= 0 AND snapshot_version >= 0", name="ck_cursor_values"),
)

Index("ix_lines_product", metadata.tables["sale_lines"].c.product_id)
