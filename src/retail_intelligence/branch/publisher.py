"""One publisher per branch process; pending outbox rows are durable."""

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Engine, func, select

from retail_intelligence.branch.models import metadata

logger = logging.getLogger("uvicorn.error")


def status(engine: Engine) -> dict[str, Any]:
    tables = metadata.tables
    outbox, control = tables["outbox"], tables["sync_control"]
    with engine.connect() as c:
        state = dict(c.execute(select(control)).mappings().one())
        state["pending"] = c.scalar(
            select(func.count()).select_from(outbox).where(outbox.c.sent_at.is_(None))
        )
        state["last_confirmed_sequence"] = c.scalar(
            select(func.max(outbox.c.sequence)).where(outbox.c.sent_at.is_not(None))
        )
        state["last_confirmed_at"] = c.scalar(select(func.max(outbox.c.sent_at)))
        return state


def set_error(engine: Engine, message: str | None, blocked: bool = False) -> None:
    table = metadata.tables["sync_control"]
    with engine.begin() as c:
        c.execute(table.update().where(table.c.id == 1).values(last_error=message, blocked=blocked))


async def publish_once(engine: Engine, base_url: str, client: httpx.AsyncClient) -> str:
    tables = metadata.tables
    outbox, control = tables["outbox"], tables["sync_control"]
    with engine.connect() as c:
        settings = c.execute(select(control)).mappings().one()
        if settings["paused"] or settings["blocked"]:
            return "paused" if settings["paused"] else "blocked"
        row = (
            c.execute(
                select(outbox)
                .where(outbox.c.sent_at.is_(None))
                .order_by(outbox.c.sequence)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return "idle"
        payload = row["payload"]
        row_id = row["id"]
    if not isinstance(payload, dict) or not isinstance(payload.get("occurred_at"), str):
        set_error(engine, f"Invalid stored event {row_id}: missing timestamp", blocked=True)
        logger.error("Invalid stored event %s", row_id)
        return "blocked"
    event = {
        "event_id": row_id,
        "schema_version": row["schema_version"],
        "branch_id": row["branch_id"],
        "sequence": row["sequence"],
        "occurred_at": payload["occurred_at"],
        "event_type": row["event_type"],
        "payload": payload,
    }
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/sync/events",
            json={"events": [event]},
            timeout=httpx.Timeout(5.0),
        )
        response.raise_for_status()
        results = response.json()["results"]
        if len(results) != 1 or results[0].get("event_id") != row_id:
            raise ValueError("Invalid central acknowledgment")
        result = results[0]
        if result["status"] not in ("accepted", "duplicate"):
            retryable = result.get("retryable") is True
            if result["status"] == "gap":
                expected = result.get("expected_sequence")
                if type(expected) is int and 0 < expected < row["sequence"]:
                    with engine.begin() as c:
                        missing = c.execute(
                            select(outbox.c.id).where(outbox.c.sequence == expected)
                        ).first()
                        if missing is not None:
                            c.execute(
                                outbox.update()
                                .where(outbox.c.sequence >= expected)
                                .values(sent_at=None)
                            )
                        else:
                            retryable = False
                            result["message"] = (
                                f"Missing local event at sequence {expected}; intervention required"
                            )
            set_error(
                engine, str(result.get("message", "event rejected"))[:500], blocked=not retryable
            )
            return "retry" if retryable else "blocked"
        with engine.begin() as c:
            c.execute(
                outbox.update()
                .where(outbox.c.id == row_id, outbox.c.sent_at.is_(None))
                .values(sent_at=datetime.now(UTC))
            )
            c.execute(control.update().where(control.c.id == 1).values(last_error=None))
        return "sent"
    except httpx.HTTPStatusError as error:
        code = error.response.status_code
        permanent = 400 <= code < 500 and code not in (408, 429)
        set_error(engine, f"HTTP {code}"[:500], blocked=permanent)
        return "blocked" if permanent else "retry"
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        set_error(engine, f"{type(error).__name__}: {error}"[:500])
        logger.warning("Synchronization failed: %s", error)
        return "retry"


async def run_publisher(engine: Engine, base_url: str, stop: asyncio.Event) -> None:
    delay = 0.5
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            try:
                outcome = await publish_once(engine, base_url, client)
                delay = min(delay * 2, 30.0) if outcome == "retry" else 0.5
            except Exception:
                logger.exception("Publisher loop failed")
                delay = min(delay * 2, 30.0)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay * random.uniform(0.8, 1.2))
            except TimeoutError:
                pass
