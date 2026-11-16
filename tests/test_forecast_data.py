import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from retail_intelligence.branch.models import metadata as branch_metadata
from retail_intelligence.forecasting.datasets import (
    aggregate_daily,
    load_dataset,
    make_metadata,
    operational_rows,
    save_dataset,
    synthetic_rows,
)


def test_synthetic_reproducible_and_all_thirty_daily_series(tmp_path):
    end = date(2026, 11, 15)
    start = end - timedelta(days=179)
    rows = synthetic_rows(42, start, end)
    assert rows == synthetic_rows(42, start, end)
    assert rows != synthetic_rows(43, start, end)
    assert len(rows) == 5400
    assert len({(r["business_date"], r["branch_id"], r["product_id"]) for r in rows}) == 5400
    assert {(r["branch_id"], r["product_id"]) for r in rows} == {
        (b, p) for b in range(1, 4) for p in range(1, 11)
    }
    assert all(r["complete"] and r["units"] >= 0 for r in rows)
    metadata = make_metadata("synthetic", start, end, seed=42)
    dataset = save_dataset(tmp_path, metadata, rows)
    assert save_dataset(tmp_path, metadata, rows) == dataset
    loaded_meta, loaded_rows = load_dataset(dataset)
    assert loaded_rows == rows
    assert loaded_meta["coverage_summary"]["complete_rows"] == 5400
    assert len(list(tmp_path.iterdir())) == 1
    assert (
        save_dataset(
            tmp_path,
            make_metadata("synthetic", start, end, seed=43),
            synthetic_rows(43, start, end),
        )
        != dataset
    )


def test_rejects_short_data_and_duplicate_keys(tmp_path):
    with pytest.raises(ValueError, match="180"):
        synthetic_rows(1, date(2026, 1, 1), date(2026, 1, 2))
    rows = synthetic_rows(1, date(2026, 1, 1), date(2026, 6, 29))
    with pytest.raises(ValueError, match="duplicate"):
        save_dataset(tmp_path, {}, rows + rows[:1])


def test_observed_sales_use_commercial_day_without_inventing_zero(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    branch_metadata.create_all(engine)
    tables = branch_metadata.tables
    with engine.begin() as connection:
        connection.execute(tables["branches"].insert().values(id=1, name="S1"))
        connection.execute(
            tables["products"].insert().values(id=1, sku="P1", name="P1", price_cents=50)
        )
        connection.execute(
            tables["sales"]
            .insert()
            .values(
                id="s",
                branch_id=1,
                occurred_at=datetime(2026, 1, 2, 2, 30, tzinfo=UTC),
                business_date=date(2026, 1, 1),
            )
        )
        connection.execute(
            tables["sale_lines"]
            .insert()
            .values(sale_id="s", line_no=1, product_id=1, quantity=2, unit_price_cents=50)
        )
    rows = operational_rows(engine, date(2026, 1, 1), date(2026, 1, 2))
    selected = [r for r in rows if r["branch_id"] == 1 and r["product_id"] == 1]
    assert [r["units"] for r in selected] == [2, None]
    assert all(not r["complete"] for r in selected)
    verified = aggregate_daily(
        {(date(2026, 1, 1), 1, 1): 2},
        [date(2026, 1, 1), date(2026, 1, 2)],
        frozenset({(1, date(2026, 1, 2))}),
    )
    selected = [r for r in verified if r["branch_id"] == 1 and r["product_id"] == 1]
    assert selected[0]["units"] == 2 and not selected[0]["complete"]
    assert selected[1]["units"] == 0 and selected[1]["complete"]
    assert (
        next(
            r
            for r in verified
            if r["branch_id"] == 2 and r["product_id"] == 1 and r["business_date"] == "2026-01-02"
        )["units"]
        is None
    )
    engine.dispose()


def test_cli_generate_prepare_query_without_duplicates(tmp_path):
    def cli(*args):
        proc = subprocess.run(
            [sys.executable, "-m", "retail_intelligence.cli", *args],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        return proc.stdout.strip()

    root = tmp_path / "datasets"
    params = (
        "forecast-generate",
        "--seed",
        "17",
        "--end-date",
        "2026-11-15",
        "--output-dir",
        str(root),
    )
    dataset = Path(cli(*params))
    assert cli(*params) == str(dataset)
    assert cli(
        "forecast-prepare",
        "--source",
        "synthetic",
        "--input",
        str(dataset),
        "--output-dir",
        str(root),
    ) == str(dataset)
    output = cli("forecast-query", "--dataset", str(dataset), "--branch", "2", "--product", "3")
    assert len(output.splitlines()) == 181
    meta = json.loads((dataset / "metadata.json").read_text(encoding="utf-8"))
    assert meta["origin"] == "synthetic" and meta["seed"] == 17
    assert len(list(root.iterdir())) == 1
