"""Initial central schema. Frozen DDL, independent of runtime models."""

import sqlalchemy as sa
from alembic import op

revision = "central_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "price_cents >= 0 AND price_cents = CAST(price_cents AS INTEGER)",
            name="ck_product_price",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_table(
        "processed_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_processed_sequence"),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id", "sequence", name="uq_processed_sequence"),
    )
    op.create_table(
        "projected_stock",
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity >= 0 AND quantity = CAST(quantity AS INTEGER)",
            name="ck_projected_stock_quantity",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.PrimaryKeyConstraint("branch_id", "product_id"),
    )
    op.create_table(
        "sales",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id", "id", name="uq_sale_branch_id"),
    )
    op.create_index("ix_sales_branch_date", "sales", ["branch_id", "business_date"], unique=False)
    op.create_table(
        "sync_cursors",
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("last_sequence >= 0 AND snapshot_version >= 0", name="ck_cursor_values"),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )
    op.create_table(
        "sale_lines",
        sa.Column("sale_id", sa.String(length=36), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "line_no > 0 AND quantity > 0 AND quantity = CAST(quantity AS INTEGER) "
            "AND unit_price_cents >= 0 "
            "AND unit_price_cents = CAST(unit_price_cents AS INTEGER)",
            name="ck_line_values",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sale_id"],
            ["sales.id"],
        ),
        sa.PrimaryKeyConstraint("sale_id", "line_no"),
    )
    op.create_index("ix_lines_product", "sale_lines", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_lines_product", table_name="sale_lines")
    op.drop_table("sale_lines")
    op.drop_table("sync_cursors")
    op.drop_index("ix_sales_branch_date", table_name="sales")
    op.drop_table("sales")
    op.drop_table("projected_stock")
    op.drop_table("processed_events")
    op.drop_table("products")
    op.drop_table("branches")
