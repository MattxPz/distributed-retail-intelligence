import asyncio

import httpx
from sqlalchemy import text

from retail_intelligence.branch.publisher import publish_once, status
from retail_intelligence.shared.bootstrap import initialize
from retail_intelligence.shared.health import database_engine


def test_publisher_lost_ack_then_duplicate(tmp_path):
    path = tmp_path / "branch.sqlite3"
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        import json

        event = json.loads(request.content)["events"][0]
        return httpx.Response(
            200, json={"results": [{"event_id": event["event_id"], "status": "duplicate"}]}
        )

    async def send(engine):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await publish_once(engine, "http://central", client)

    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        assert asyncio.run(send(engine)) == "retry"
        assert status(engine)["pending"] == 1
    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        assert asyncio.run(send(engine)) == "sent"
        assert status(engine)["pending"] == 0
        assert status(engine)["last_confirmed_sequence"] == 1
    assert requests[0].content == requests[1].content


def test_publisher_block_and_pause_survive_restart(tmp_path):
    path = tmp_path / "branch.sqlite3"

    async def run(engine):
        def handler(request):
            import json

            item = json.loads(request.content)["events"][0]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "event_id": item["event_id"],
                            "status": "invalid",
                            "retryable": False,
                            "message": "bad payload",
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await publish_once(engine, "http://central", client) == "blocked"

    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        asyncio.run(run(engine))
        with engine.begin() as c:
            c.execute(text("UPDATE sync_control SET paused=true"))
    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        assert status(engine)["paused"] is True
        assert status(engine)["blocked"] is True
        assert status(engine)["pending"] == 1
        assert status(engine)["last_error"] == "bad payload"


def test_gap_requeues_missing_acknowledged_event(tmp_path):
    from datetime import UTC, datetime

    from retail_intelligence.branch.sales import SaleInput, create_sale

    path = tmp_path / "branch.sqlite3"

    async def run(engine):
        def handler(request):
            import json

            item = json.loads(request.content)["events"][0]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "event_id": item["event_id"],
                            "status": "gap",
                            "retryable": True,
                            "expected_sequence": 1,
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await publish_once(engine, "http://central", client) == "retry"

    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        create_sale(
            engine,
            1,
            "key",
            SaleInput.model_validate({"lines": [{"product_id": 1, "quantity": 1}]}),
        )
        with engine.begin() as c:
            c.execute(
                text("UPDATE outbox SET sent_at=:sent WHERE sequence=1"),
                {"sent": datetime.now(UTC)},
            )
        asyncio.run(run(engine))
        assert status(engine)["pending"] == 2
        assert status(engine)["blocked"] is False


def test_invalid_stored_payload_is_blocked_and_visible(tmp_path):
    path = tmp_path / "branch.sqlite3"

    async def send(engine):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            return await publish_once(engine, "http://central", client)

    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        initialize(engine, "branch", 1)
        with engine.begin() as connection:
            connection.execute(text("UPDATE outbox SET payload='{}' WHERE sequence=1"))
        assert asyncio.run(send(engine)) == "blocked"
        assert status(engine)["blocked"] is True
        assert status(engine)["pending"] == 1
        assert "missing timestamp" in status(engine)["last_error"]
