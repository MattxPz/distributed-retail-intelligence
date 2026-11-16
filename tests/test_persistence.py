import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from retail_intelligence.branch.models import metadata as branch_metadata
from retail_intelligence.central.models import metadata as central_metadata
from retail_intelligence.central.snapshots import apply_initial_snapshot
from retail_intelligence.shared import bootstrap
from retail_intelligence.shared.events import InitialSnapshot, StockItem
from retail_intelligence.shared.health import database_engine


def pytest_generate_tests(metafunc):
    if "database" in metafunc.fixturenames:
        roles = {
            "test_local_database_cannot_change_branch": ["branch"],
            "test_outbox_snapshot_matches_initial_inventory": ["branch"],
            "test_snapshot_projection_deduplication_and_old_snapshot": ["central"],
            "test_snapshot_failure_does_not_leave_partial_projection": ["central"],
            "test_concurrent_snapshot_delivery": ["central"],
            "test_event_receiver_end_to_end": ["central"],
            "test_http_loss_replay_and_partial_batch": ["central"],
            "test_local_outbox_is_the_source_of_central_inventory": ["central"],
        }
        metafunc.parametrize(
            "database", roles.get(metafunc.function.__name__, ["branch", "central"]), indirect=True
        )


@pytest.fixture
def database(request, tmp_path):
    role = request.param
    if role == "branch":
        with database_engine(f"sqlite:///{(tmp_path / 'branch.sqlite3').as_posix()}") as engine:
            yield role, engine
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Real PostgreSQL required: run compose.test.yaml")
    schema = "test_" + uuid4().hex
    with database_engine(url) as admin:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        try:
            with database_engine(scoped.render_as_string(hide_password=False)) as engine:
                assert engine.dialect.name == "postgresql"
                yield role, engine
        finally:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def init(role, engine):
    bootstrap.initialize(engine, role, 1 if role == "branch" else None)


def test_new_database_second_run_and_no_stock_replenishment(database):
    role, engine = database
    init(role, engine)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM branches")) == 3
        assert connection.scalar(text("SELECT count(*) FROM products")) == 10
        if role == "branch":
            assert connection.scalar(text("SELECT sum(quantity) FROM inventory")) == 1000
            before = connection.scalar(text("SELECT payload FROM outbox"))
            connection.execute(
                text("INSERT INTO sales VALUES ('sold',1,'2026-11-14 12:00:00+00:00','2026-11-14')")
            )
            connection.execute(text("INSERT INTO sale_lines VALUES ('sold',1,1,3,150)"))
            connection.execute(text("UPDATE inventory SET quantity=97 WHERE product_id=1"))
        else:
            assert connection.scalar(text("SELECT count(*) FROM projected_stock")) == 0
            assert connection.scalar(text("SELECT count(*) FROM sync_cursors")) == 3
        connection.execute(text("UPDATE products SET price_cents=999 WHERE id=1"))
    init(role, engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT price_cents FROM products WHERE id=1")) == 999
        assert connection.scalar(text("SELECT count(*) FROM products")) == 10
        assert connection.scalar(text("SELECT count(*) FROM branches")) == 3
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == role + "_0002"
        if role == "branch":
            assert (
                connection.scalar(text("SELECT quantity FROM inventory WHERE product_id=1")) == 97
            )
            assert connection.scalar(text("SELECT count(*) FROM outbox")) == 1
            assert connection.scalar(text("SELECT payload FROM outbox")) == before
            assert connection.scalar(text("SELECT next_sequence FROM local_state")) == 2
        metadata = branch_metadata if role == "branch" else central_metadata
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO products VALUES (11, 'SKU-001', 'duplicate', 10)",
        "UPDATE products SET price_cents=-1 WHERE id=1",
        "INSERT INTO sales VALUES ('bad', 999, '2026-11-14 12:00:00+00:00', '2026-11-14')",
        "INSERT INTO sale_lines VALUES ('missing', 1, 1, 1, 100)",
        "INSERT INTO sale_lines VALUES ('sale', 1, 999, 1, 100)",
        "INSERT INTO sale_lines VALUES ('sale', 1, 1, 0, 100)",
        "INSERT INTO sale_lines VALUES ('sale', 1, 1, -1, 100)",
        "INSERT INTO sale_lines VALUES ('sale', 1, 1, 1, -100)",
    ],
)
def test_common_constraints_in_real_databases(database, statement):
    role, engine = database
    init(role, engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO sales VALUES ('sale', 1, '2026-11-14 12:00:00+00:00', '2026-11-14')")
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(statement))


def test_stock_uniqueness_nonnegative_and_historical_price(database):
    role, engine = database
    init(role, engine)
    stock = "inventory" if role == "branch" else "projected_stock"
    with engine.begin() as connection:
        if role == "central":
            connection.execute(text("INSERT INTO projected_stock VALUES (1,1,100)"))
        connection.execute(
            text("INSERT INTO sales VALUES ('sale', 1, '2026-11-14 12:00:00+00:00', '2026-11-14')")
        )
        connection.execute(text("INSERT INTO sale_lines VALUES ('sale',1,1,2,150)"))
        connection.execute(text("UPDATE products SET price_cents=900 WHERE id=1"))
        assert connection.scalar(text("SELECT unit_price_cents FROM sale_lines")) == 150
    for statement in [
        f"INSERT INTO {stock} VALUES (1,1,100)",
        f"UPDATE {stock} SET quantity=-1 WHERE product_id=1",
    ]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement))
    if role == "branch":
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE inventory SET quantity=1.5 WHERE product_id=1"))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO idempotency_keys VALUES (1,'key','hash','sale')"))
        for statement in [
            "INSERT INTO idempotency_keys VALUES (1,'key','hash','sale')",
            "INSERT INTO idempotency_keys VALUES (2,'key','hash','sale')",
        ]:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(statement))


def test_startup_rolls_back_schema_and_seed_on_failure(database, monkeypatch):
    role, engine = database
    original = bootstrap.seed_branch if role == "branch" else bootstrap.seed_central

    def fail(*args):
        original(*args)
        raise RuntimeError("Injected seed failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(bootstrap, "seed_" + role, fail)
        with pytest.raises(RuntimeError, match="Injected"):
            init(role, engine)
    assert inspect(engine).get_table_names() == []
    init(role, engine)


def test_two_processes_can_initialize_same_empty_database(database):
    role, engine = database
    env = dict(
        os.environ, DATABASE_URL=engine.url.render_as_string(hide_password=False), BRANCH_ID="1"
    )
    command = [sys.executable, "-m", "retail_intelligence.cli", "init", role]
    processes = [
        subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    for process in processes:
        output = process.communicate(timeout=30)
        assert process.returncode == 0, output
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM products")) == 10
        if role == "branch":
            assert connection.scalar(text("SELECT count(*) FROM outbox")) == 1


def test_local_database_cannot_change_branch(database):
    role, engine = database
    init(role, engine)
    with pytest.raises(ValueError, match="different branch"):
        bootstrap.initialize(engine, "branch", 2)


def snapshot():
    return InitialSnapshot(
        id=uuid4(),
        branch_id=1,
        occurred_at=datetime.now(UTC),
        items=[StockItem(product_id=i, quantity=100) for i in range(1, 11)],
    )


def test_snapshot_projection_deduplication_and_old_snapshot(database):
    role, engine = database
    init(role, engine)
    event = snapshot()
    assert apply_initial_snapshot(engine, event)
    with engine.begin() as connection:
        connection.execute(text("UPDATE projected_stock SET quantity=90 WHERE product_id=1"))
        connection.execute(text("UPDATE sync_cursors SET last_sequence=2 WHERE branch_id=1"))
    assert not apply_initial_snapshot(engine, event)
    with pytest.raises(ValueError, match="cannot replace"):
        apply_initial_snapshot(engine, snapshot())
    changed = event.model_copy(deep=True)
    changed.items[0].quantity = 99
    with pytest.raises(ValueError, match="identity conflict"):
        apply_initial_snapshot(engine, changed)
    init(role, engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT quantity FROM projected_stock WHERE product_id=1")) == 90
        )
        assert connection.scalar(text("SELECT count(*) FROM processed_events")) == 1
        assert (
            connection.scalar(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=1")) == 2
        )


def test_snapshot_failure_does_not_leave_partial_projection(database):
    role, engine = database
    init(role, engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM products WHERE id=10"))
    with pytest.raises(IntegrityError):
        apply_initial_snapshot(engine, snapshot())
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projected_stock")) == 0
        assert connection.scalar(text("SELECT count(*) FROM processed_events")) == 0
        assert (
            connection.scalar(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=1")) == 0
        )


def test_outbox_snapshot_matches_initial_inventory(database):
    role, engine = database
    init(role, engine)
    with engine.connect() as connection:
        payload = connection.scalar(select(branch_metadata.tables["outbox"].c.payload))
        event = InitialSnapshot.model_validate(payload)
        rows = connection.execute(
            text("SELECT product_id, quantity FROM inventory ORDER BY product_id")
        ).all()
        assert [(item.product_id, item.quantity) for item in event.items] == [
            tuple(row) for row in rows
        ]
        assert event.sequence == 1


def test_event_sequence_and_cursor_constraints(database):
    role, engine = database
    init(role, engine)
    if role == "branch":
        statements = [
            "UPDATE outbox SET sequence=0",
            "INSERT INTO outbox SELECT 'duplicate',branch_id,sequence,schema_version,"
            "event_type,occurred_at,payload,sent_at FROM outbox",
            "UPDATE local_state SET next_sequence=1",
        ]
    else:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO processed_events VALUES ('event',1,1,'hash')"))
        statements = [
            "INSERT INTO processed_events VALUES ('duplicate',1,1,'hash')",
            "INSERT INTO processed_events VALUES ('event',2,1,'hash')",
            "UPDATE sync_cursors SET last_sequence=-1 WHERE branch_id=1",
        ]
    for statement in statements:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement))


def test_concurrent_snapshot_delivery(database):
    role, engine = database
    init(role, engine)
    event = snapshot()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply_initial_snapshot(engine, event), range(2)))
    assert sorted(results) == [False, True]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projected_stock")) == 10
        assert connection.scalar(text("SELECT count(*) FROM processed_events")) == 1


def test_local_outbox_is_the_source_of_central_inventory(database, tmp_path):
    role, central = database
    init(role, central)
    with database_engine(f"sqlite:///{(tmp_path / 'source.sqlite3').as_posix()}") as branch:
        bootstrap.initialize(branch, "branch", 2)
        with branch.connect() as connection:
            payload = connection.scalar(select(branch_metadata.tables["outbox"].c.payload))
            event = InitialSnapshot.model_validate(payload)
        assert apply_initial_snapshot(central, event)
        assert not apply_initial_snapshot(central, event)
    with central.connect() as connection:
        assert (
            connection.scalar(text("SELECT sum(quantity) FROM projected_stock WHERE branch_id=2"))
            == 1000
        )
        assert (
            connection.scalar(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=2")) == 1
        )
        assert (
            connection.scalar(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=1")) == 0
        )


def test_event_receiver_end_to_end(database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    from retail_intelligence.branch.sales import SaleInput, create_sale
    from retail_intelligence.central.receiver import EventError, process_event

    role, central = database
    init(role, central)
    with database_engine(f"sqlite:///{(tmp_path / 'source-sync.sqlite3').as_posix()}") as branch:
        bootstrap.initialize(branch, "branch", 1)
        create_sale(
            branch,
            1,
            "key",
            SaleInput.model_validate({"lines": [{"product_id": 1, "quantity": 2}]}),
        )
        with branch.connect() as c:
            events = (
                c.execute(
                    select(branch_metadata.tables["outbox"].c.payload).order_by(
                        branch_metadata.tables["outbox"].c.sequence
                    )
                )
                .scalars()
                .all()
            )
        with pytest.raises(EventError, match="expected sequence 1") as exc:
            process_event(central, events[1])
        assert exc.value.retryable
        with central.connect() as c:
            assert c.scalar(text("SELECT count(*) FROM processed_events")) == 0
            assert c.scalar(text("SELECT count(*) FROM projected_stock")) == 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: process_event(central, events[0]), range(2)))
        assert sorted(outcomes) == ["accepted", "duplicate"]

        def fail(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE SYNC_CURSORS"):
                raise RuntimeError("injected before commit")

        event.listen(central, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="before commit"):
                process_event(central, events[1])
        finally:
            event.remove(central, "before_cursor_execute", fail)
        with central.connect() as c:
            assert (
                c.scalar(
                    text("SELECT quantity FROM projected_stock WHERE branch_id=1 AND product_id=1")
                )
                == 100
            )
            assert c.scalar(text("SELECT count(*) FROM sales")) == 0
            assert c.scalar(text("SELECT count(*) FROM processed_events")) == 1
        assert process_event(central, events[1]) == "accepted"
        assert process_event(central, events[1]) == "duplicate"
        changed = dict(events[1], total_cents=999)
        with pytest.raises(EventError):
            process_event(central, changed)
        bootstrap.initialize(central, "central")
        assert process_event(central, events[1]) == "duplicate"
        with central.connect() as c:
            assert (
                c.scalar(
                    text("SELECT quantity FROM projected_stock WHERE branch_id=1 AND product_id=1")
                )
                == 98
            )
            assert c.scalar(text("SELECT count(*) FROM sales")) == 1
            assert c.scalar(text("SELECT count(*) FROM sale_lines")) == 1
            assert c.scalar(text("SELECT count(*) FROM processed_events")) == 2
            assert c.scalar(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=1")) == 2


def test_http_loss_replay_and_partial_batch(database, tmp_path):
    import asyncio
    import json

    import httpx

    from retail_intelligence.branch.publisher import publish_once, status
    from retail_intelligence.branch.sales import SaleInput, create_sale
    from retail_intelligence.central.app import app as central_app

    role, central = database
    init(role, central)
    central_app.state.engine = central
    with database_engine(f"sqlite:///{(tmp_path / 'http-branch.sqlite3').as_posix()}") as branch:
        bootstrap.initialize(branch, "branch", 1)
        create_sale(
            branch,
            1,
            "sale",
            SaleInput.model_validate({"lines": [{"product_id": 1, "quantity": 2}]}),
        )

        class LostFirstResponse(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.ASGITransport(app=central_app)
                self.first = True

            async def handle_async_request(self, request):
                response = await self.inner.handle_async_request(request)
                if self.first:
                    self.first = False
                    await response.aclose()
                    raise httpx.ReadTimeout("response lost after commit", request=request)
                return response

        async def scenario():
            async with httpx.AsyncClient(transport=LostFirstResponse()) as client:
                assert await publish_once(branch, "http://central", client) == "retry"
                assert status(branch)["pending"] == 2
                assert await publish_once(branch, "http://central", client) == "sent"
                assert await publish_once(branch, "http://central", client) == "sent"
                assert status(branch)["pending"] == 0
                assert status(branch)["last_confirmed_sequence"] == 2
                with branch.connect() as c:
                    events = (
                        c.execute(
                            select(branch_metadata.tables["outbox"].c.payload).order_by(
                                branch_metadata.tables["outbox"].c.sequence
                            )
                        )
                        .scalars()
                        .all()
                    )
                changed = json.loads(json.dumps(events[1]))
                changed["lines"][0]["unit_price_cents"] += 1
                changed["total_cents"] += changed["lines"][0]["quantity"]

                def envelope(payload):
                    return {
                        "event_id": payload["id"],
                        "branch_id": payload["branch_id"],
                        "sequence": payload["sequence"],
                        "schema_version": payload["schema_version"],
                        "occurred_at": payload["occurred_at"],
                        "event_type": payload["event_type"],
                        "payload": payload,
                    }

                response = await client.post(
                    "http://central/sync/events",
                    json={"events": [envelope(events[0]), envelope(changed), envelope(events[1])]},
                )
                assert [item["status"] for item in response.json()["results"]] == [
                    "duplicate",
                    "conflict",
                    "duplicate",
                ]
                central_state = await client.get("http://central/sync/status")
                assert central_state.status_code == 200
                assert central_state.json()[0]["last_sequence"] == 2
                assert central_state.json()[0]["stale"] is False

        asyncio.run(scenario())
        with central.connect() as c:
            assert (
                c.scalar(
                    text("SELECT quantity FROM projected_stock WHERE branch_id=1 AND product_id=1")
                )
                == 98
            )
            assert c.scalar(text("SELECT count(*) FROM sales")) == 1
            assert c.scalar(text("SELECT count(*) FROM processed_events")) == 2
