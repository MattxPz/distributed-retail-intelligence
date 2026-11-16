"""Deterministic, separate forecasting datasets and conservative operational coverage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select

from retail_intelligence.central.models import metadata

ZONE = ZoneInfo("America/Guayaquil")
FORMAT_VERSION = "daily-v1"
DEMO_END_DATE = date(2026, 11, 15)
BRANCHES = range(1, 4)
PRODUCTS = range(1, 11)
FIELDS = ["business_date", "branch_id", "product_id", "units", "complete", "coverage_reason"]


def days_between(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start must be on or before end")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def synthetic_rows(seed: int, start: date, end: date) -> list[dict[str, Any]]:
    days = days_between(start, end)
    if len(days) < 180:
        raise ValueError("synthetic datasets require at least 180 days")
    rows = []
    weekly = (0.80, 0.85, 0.90, 0.95, 1.10, 1.35, 1.05)
    for branch in BRANCHES:
        for product in PRODUCTS:
            # Separate RNG per series makes reproducibility independent of iteration order.
            rng = random.Random(f"{FORMAT_VERSION}:{seed}:{branch}:{product}")
            for day in days:
                base = (3.0 + product * 1.4) * (0.8 + branch * 0.2)
                units = max(
                    0, round(base * weekly[day.weekday()] + rng.gauss(0, 1.5 + product * 0.13))
                )
                rows.append(
                    {
                        "business_date": day.isoformat(),
                        "branch_id": branch,
                        "product_id": product,
                        "units": units,
                        "complete": True,
                        "coverage_reason": "synthetic_full",
                    }
                )
    return rows


def operational_rows(engine: Engine, start: date, end: date) -> list[dict[str, Any]]:
    """Aggregate actual sales; central cursors alone cannot certify a closed business day."""
    days = days_between(start, end)
    sales, lines = metadata.tables["sales"], metadata.tables["sale_lines"]
    totals: defaultdict[tuple[date, int, int], int] = defaultdict(int)
    with engine.connect() as connection:
        data = connection.execute(
            select(
                sales.c.occurred_at, sales.c.branch_id, lines.c.product_id, lines.c.quantity
            ).select_from(sales.join(lines, lines.c.sale_id == sales.c.id))
        )
        for occurred_at, branch, product, quantity in data:
            # SQLite timestamps have no offset; the stored event timestamp is UTC.
            occurred_at = cast(datetime, occurred_at)
            branch = cast(int, branch)
            product = cast(int, product)
            quantity = cast(int, quantity)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
            commercial = occurred_at.astimezone(ZONE).date()
            if start <= commercial <= end and branch in BRANCHES and product in PRODUCTS:
                totals[(commercial, branch, product)] += quantity
    return aggregate_daily(totals, days, frozenset())


def aggregate_daily(
    totals: dict[tuple[date, int, int], int],
    days: list[date],
    verified_days: frozenset[tuple[int, date]],
) -> list[dict[str, Any]]:
    """Only callers with independent, complete day evidence may certify a day."""
    rows = []
    for branch in BRANCHES:
        for product in PRODUCTS:
            for day in days:
                key = (day, branch, product)
                complete = (branch, day) in verified_days
                rows.append(
                    {
                        "business_date": day.isoformat(),
                        "branch_id": branch,
                        "product_id": product,
                        "units": totals.get(key, 0) if complete else totals.get(key),
                        "complete": complete,
                        "coverage_reason": "independently_verified"
                        if complete
                        else "unverified_sync_coverage",
                    }
                )
    return rows


def make_metadata(
    origin: str, start: date, end: date, *, seed: int | None = None
) -> dict[str, Any]:
    return {
        "schema_version": FORMAT_VERSION,
        "generator_version": "synthetic-v1" if origin == "synthetic" else None,
        "origin": origin,
        "seed": seed,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": str(ZONE),
        "coverage_policy": "synthetic_complete" if origin == "synthetic" else "unverified_all_days",
        "units_semantics": "synthetic_demand"
        if origin == "synthetic"
        else "observed_sales_lower_bound",
    }


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **row,
                "complete": str(row["complete"]).lower(),
                "units": "" if row["units"] is None else row["units"],
            }
        )
    return buffer.getvalue().encode("utf-8")


def save_dataset(root: Path, meta: dict[str, Any], rows: list[dict[str, Any]]) -> Path:
    data = csv_bytes(rows)
    keys = [(r["business_date"], r["branch_id"], r["product_id"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate daily series key")
    body_hash = hashlib.sha256(data).hexdigest()
    description = {
        **meta,
        "row_count": len(rows),
        "csv_sha256": body_hash,
        "coverage_summary": {
            "complete_rows": sum(1 for row in rows if row["complete"]),
            "unknown_rows": sum(1 for row in rows if not row["complete"]),
            "rows_with_observed_units": sum(1 for row in rows if row["units"] is not None),
        },
    }
    identity = hashlib.sha256(
        json.dumps(description, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    description["dataset_id"] = identity
    directory = root / identity
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        "daily.csv": data,
        "metadata.json": (
            json.dumps(description, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
    }
    for name, content in outputs.items():
        file = directory / name
        try:
            with file.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            if file.read_bytes() != content:
                raise ValueError(f"existing dataset content differs: {file}") from None
    return directory


def load_dataset(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    data = (directory / "daily.csv").read_bytes()
    if hashlib.sha256(data).hexdigest() != meta["csv_sha256"]:
        raise ValueError("dataset checksum mismatch")
    desc = {key: value for key, value in meta.items() if key != "dataset_id"}
    identity = hashlib.sha256(
        json.dumps(desc, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if meta["dataset_id"] != identity or directory.name != identity:
        raise ValueError("dataset identity mismatch")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    if reader.fieldnames != FIELDS:
        raise ValueError("invalid dataset fields")
    rows = [
        {
            **r,
            "branch_id": int(r["branch_id"]),
            "product_id": int(r["product_id"]),
            "units": int(r["units"]) if r["units"] else None,
            "complete": r["complete"] == "true",
        }
        for r in reader
    ]
    if len(rows) != meta["row_count"]:
        raise ValueError("dataset row count mismatch")
    return meta, rows
