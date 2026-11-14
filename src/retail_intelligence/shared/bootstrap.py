"""Serialized, atomic migration + seed for one database."""

import logging
from importlib.resources import files
from pathlib import Path
from threading import RLock
from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from retail_intelligence.branch.seed import seed as seed_branch
from retail_intelligence.central.seed import seed as seed_central

_migration_lock = RLock()  # Alembic's context proxy is process-global.
logger = logging.getLogger("uvicorn.error")


def initialize(
    engine: Engine, role: Literal["branch", "central"], branch_id: int | None = None
) -> None:
    expected = "sqlite" if role == "branch" else "postgresql"
    if engine.dialect.name != expected:
        raise ValueError(f"{role} requires {expected}")
    config = Config()
    migration_path = Path(str(files("retail_intelligence"))) / "migrations" / role
    if not migration_path.is_dir():
        migration_path = Path(__file__).resolve().parents[3] / "migrations" / role
    config.set_main_option("script_location", str(migration_path))
    with _migration_lock, engine.connect() as connection:
        try:
            if role == "branch":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.execute(text("SET LOCAL lock_timeout = '10s'"))
                connection.execute(text("SELECT pg_advisory_xact_lock(74123901)"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            if role == "branch":
                if branch_id is None:
                    raise ValueError("BRANCH_ID is required")
                seed_branch(connection, branch_id)
            else:
                seed_central(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    logger.info("%s database migrated and seeded (branch=%s)", role, branch_id)
