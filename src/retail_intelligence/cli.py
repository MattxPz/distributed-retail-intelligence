import argparse
import logging

from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import BranchSettings, CentralSettings
from retail_intelligence.shared.health import database_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init"])
    parser.add_argument("role", choices=["branch", "central"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
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
