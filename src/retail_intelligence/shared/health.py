import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


@contextmanager
def database_engine(url: str) -> Iterator[Engine]:
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3} if url.startswith("postgresql+") else {},
    )
    try:
        yield engine
    finally:
        engine.dispose()


def check_readiness(engine: Engine) -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready"}
