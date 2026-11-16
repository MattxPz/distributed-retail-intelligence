import argparse
import logging

from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import BranchSettings, CentralSettings
from retail_intelligence.shared.health import database_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "demo-offline"])
    parser.add_argument("role", nargs="?", choices=["branch", "central"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "demo-offline":
        if args.role is not None:
            parser.error("demo-offline does not take a role")
        from retail_intelligence.demo_offline import run_demo

        run_demo()
        return
    if args.role is None:
        parser.error("init requires branch or central")
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
