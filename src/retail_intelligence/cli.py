import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import BranchSettings, CentralSettings
from retail_intelligence.shared.health import database_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("role", choices=["branch", "central"])
    commands.add_parser("demo-offline")
    generate = commands.add_parser("forecast-generate")
    generate.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 11, 15))
    generate.add_argument("--days", type=int, default=180)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--output-dir", type=Path, default=Path("data/generated/datasets"))
    prepare = commands.add_parser("forecast-prepare")
    prepare.add_argument("--source", choices=["synthetic", "operational"], required=True)
    prepare.add_argument("--input", type=Path)
    prepare.add_argument("--start-date", type=date.fromisoformat)
    prepare.add_argument("--end-date", type=date.fromisoformat)
    prepare.add_argument("--output-dir", type=Path, default=Path("data/generated/datasets"))
    query = commands.add_parser("forecast-query")
    query.add_argument("--dataset", required=True, type=Path)
    query.add_argument("--branch", type=int, choices=range(1, 4), required=True)
    query.add_argument("--product", type=int, choices=range(1, 11), required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "demo-offline":
        from retail_intelligence.demo_offline import run_demo

        run_demo()
        return
    if args.command.startswith("forecast-"):
        from retail_intelligence.forecasting.datasets import (
            csv_bytes,
            load_dataset,
            make_metadata,
            operational_rows,
            save_dataset,
            synthetic_rows,
        )

        if args.command == "forecast-generate":
            if args.days < 180:
                parser.error("--days must be at least 180")
            start = args.end_date - timedelta(days=args.days - 1)
            output = save_dataset(
                args.output_dir,
                make_metadata("synthetic", start, args.end_date, seed=args.seed),
                synthetic_rows(args.seed, start, args.end_date),
            )
            print(output)
        elif args.command == "forecast-query":
            _, rows = load_dataset(args.dataset)
            filtered = [
                row
                for row in rows
                if row["branch_id"] == args.branch and row["product_id"] == args.product
            ]
            print(csv_bytes(filtered).decode("utf-8"), end="")
        elif args.source == "synthetic":
            if args.input is None or args.start_date or args.end_date:
                parser.error("synthetic preparation requires --input only")
            meta, rows = load_dataset(args.input)
            if meta["origin"] != "synthetic":
                parser.error("input dataset must be synthetic")
            base = {
                k: v
                for k, v in meta.items()
                if k not in ("row_count", "csv_sha256", "dataset_id", "coverage_summary")
            }
            print(save_dataset(args.output_dir, base, rows))
        else:
            if args.input is not None or args.start_date is None or args.end_date is None:
                parser.error("operational preparation requires --start-date and --end-date")
            settings = CentralSettings()  # type: ignore[call-arg]
            with database_engine(settings.database_url) as engine:
                rows = operational_rows(engine, args.start_date, args.end_date)
            print(
                save_dataset(
                    args.output_dir,
                    make_metadata("operational", args.start_date, args.end_date),
                    rows,
                )
            )
        return
    if args.role == "branch":
        branch = BranchSettings()  # type: ignore[call-arg]
        with database_engine(branch.database_url) as engine:
            initialize(engine, "branch", branch.branch_id)
    else:
        central = CentralSettings()  # type: ignore[call-arg]
        with database_engine(central.database_url) as engine:
            initialize(engine, "central")


if __name__ == "__main__":
    main()
