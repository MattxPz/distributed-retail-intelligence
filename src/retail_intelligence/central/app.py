import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from retail_intelligence.central.models import metadata
from retail_intelligence.central.receiver import EventError, process_event
from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.config import CentralSettings
from retail_intelligence.shared.health import check_readiness, database_engine

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = CentralSettings()  # type: ignore[call-arg]
    with database_engine(settings.database_url) as engine:
        initialize(engine, "central")
        app.state.engine = engine
        logger.info("Central started")
        yield


app = FastAPI(title="Central API", lifespan=lifespan)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    return check_readiness(request.app.state.engine)


class EventBatch(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=100)


@app.post("/sync/events")
def receive_events(request: Request, batch: EventBatch) -> dict[str, Any]:
    results = []
    for event in batch.events:
        try:
            payload = event.get("payload")
            if not isinstance(payload, dict) or any(
                event.get(outer) != payload.get(inner)
                for outer, inner in (
                    ("event_id", "id"),
                    ("schema_version", "schema_version"),
                    ("branch_id", "branch_id"),
                    ("sequence", "sequence"),
                    ("occurred_at", "occurred_at"),
                    ("event_type", "event_type"),
                )
            ):
                logger.warning("Invalid event envelope: id=%s", event.get("event_id"))
                raise EventError("invalid", "event envelope does not match payload")
            outcome = process_event(request.app.state.engine, payload)
            results.append(
                {"event_id": event.get("event_id"), "status": outcome, "retryable": False}
            )
        except EventError as error:
            results.append(
                {
                    "event_id": event.get("event_id"),
                    "status": error.code,
                    "retryable": error.retryable,
                    "message": str(error),
                    "expected_sequence": error.expected_sequence,
                }
            )
        except SQLAlchemyError:
            logger.exception("Event processing failed")
            results.append(
                {
                    "event_id": event.get("event_id"),
                    "status": "temporary",
                    "retryable": True,
                    "message": "database unavailable",
                }
            )
    return {"results": results}


@app.get("/sync/status")
def sync_status(request: Request) -> list[dict[str, Any]]:
    table = metadata.tables["sync_cursors"]
    with request.app.state.engine.connect() as connection:
        rows = connection.execute(select(table).order_by(table.c.branch_id)).mappings().all()
    now = datetime.now(UTC)
    return [
        {
            **dict(row),
            "stale": row["last_synced_at"] is None
            or now - row["last_synced_at"] > timedelta(minutes=5),
        }
        for row in rows
    ]
