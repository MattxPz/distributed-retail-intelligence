from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from retail_intelligence.branch.app import app
from retail_intelligence.branch.sales import SaleInput, create_sale


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BRANCH_ID", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'branch.sqlite3').as_posix()}")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def snapshot():
    with app.state.engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()
            for name in (
                "sales",
                "sale_lines",
                "inventory",
                "outbox",
                "idempotency_keys",
                "local_state",
            )
        }


def post(client, lines, key="sale-1"):
    return client.post("/sales", json={"lines": lines}, headers={"Idempotency-Key": key})


def test_sale_replay_and_queries(client):
    lines = [{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 3}]
    result = post(client, lines)
    assert result.status_code == 200
    assert result.json()["total_cents"] == 630
    first = snapshot()
    assert len(first["sales"]) == 1 and len(first["outbox"]) == 2
    assert post(client, list(reversed(lines))).json() == result.json()
    assert snapshot() == first
    assert post(client, [{"product_id": 1, "quantity": 1}]).status_code == 409
    assert snapshot() == first
    assert client.get("/products").status_code == 200
    assert len(client.get("/products").json()) == 10
    assert client.get("/inventory").json()[0]["quantity"] == 98
    assert client.get("/sales").json()[0] == result.json()
    assert client.get("/sales/" + result.json()["id"]).json() == result.json()
    assert client.get("/sales/missing").status_code == 404
    with app.state.engine.connect() as c:
        event_row = c.execute(
            text("SELECT sequence, event_type, payload FROM outbox WHERE sequence=2")
        ).one()
        assert event_row.sequence == 2 and event_row.event_type == "sale.created"
        assert '"total_cents": 630' in event_row.payload


@pytest.mark.parametrize(
    "lines,status",
    [
        ([{"product_id": 1, "quantity": 0}], 422),
        ([{"product_id": 1, "quantity": -1}], 422),
        ([{"product_id": 1, "quantity": 1.0}], 422),
        ([{"product_id": 1, "quantity": True}], 422),
        ([{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}], 422),
        ([{"product_id": 999, "quantity": 1}], 404),
        ([{"product_id": 1, "quantity": 101}], 409),
        ([{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 101}], 409),
    ],
)
def test_invalid_sales_have_no_side_effects(client, lines, status):
    before = snapshot()
    assert post(client, lines).status_code == status
    assert snapshot() == before


def test_outbox_failure_rolls_back_all_changes(client):
    before = snapshot()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO OUTBOX"):
            raise IntegrityError(statement, parameters, Exception("injected"))

    event.listen(app.state.engine, "before_cursor_execute", fail)
    try:
        assert post(client, [{"product_id": 1, "quantity": 3}]).status_code == 500
    finally:
        event.remove(app.state.engine, "before_cursor_execute", fail)
    assert snapshot() == before


def test_concurrent_different_keys_last_unit(client):
    with app.state.engine.begin() as connection:
        connection.execute(text("UPDATE inventory SET quantity=1 WHERE product_id=1"))
    barrier = Barrier(2)
    data = SaleInput.model_validate({"lines": [{"product_id": 1, "quantity": 1}]})

    def submit(key):
        barrier.wait(timeout=10)
        try:
            return create_sale(app.state.engine, 1, key, data)["id"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ["one", "two"]))
    assert sum(value == 409 for value in outcomes) == 1
    after = snapshot()
    assert len(after["sales"]) == 1 and len(after["outbox"]) == 2
    assert after["inventory"][0].quantity == 0
    assert len(after["idempotency_keys"]) == 1


def test_concurrent_same_key(client):
    barrier = Barrier(2)
    data = SaleInput.model_validate({"lines": [{"product_id": 1, "quantity": 2}]})

    def submit(_):
        barrier.wait(timeout=10)
        return create_sale(app.state.engine, 1, "same", data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(submit, range(2))
    assert first == second
    after = snapshot()
    assert len(after["sales"]) == 1 and len(after["outbox"]) == 2
    assert after["inventory"][0].quantity == 98
    assert len(after["idempotency_keys"]) == 1


def test_busy_retry_restarts_complete_transaction(client):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    attempts = []

    def fail_once(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO OUTBOX"):
            attempts.append(1)
            if len(attempts) == 1:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise OperationalError(statement, parameters, error)

    event.listen(app.state.engine, "before_cursor_execute", fail_once)
    try:
        response = post(client, [{"product_id": 1, "quantity": 2}])
    finally:
        event.remove(app.state.engine, "before_cursor_execute", fail_once)
    assert response.status_code == 200
    assert len(attempts) == 2
    after = snapshot()
    assert len(after["sales"]) == 1 and len(after["outbox"]) == 2
    assert after["inventory"][0].quantity == 98
    assert len(after["idempotency_keys"]) == 1
    assert after["local_state"][0].next_sequence == 3


def test_replay_preserves_historical_price_after_catalog_change(client):
    lines = [{"product_id": 1, "quantity": 1}]
    original = post(client, lines)
    with app.state.engine.begin() as connection:
        connection.execute(text("UPDATE products SET price_cents=999 WHERE id=1"))
    before = snapshot()
    replay = post(client, lines)
    assert replay.json() == original.json()
    assert replay.json()["total_cents"] == 150
    assert snapshot() == before


def test_missing_idempotency_key_does_not_write(client):
    before = snapshot()
    response = client.post("/sales", json={"lines": [{"product_id": 1, "quantity": 1}]})
    assert response.status_code == 422
    assert snapshot() == before
