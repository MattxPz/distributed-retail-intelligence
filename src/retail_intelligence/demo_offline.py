"""Offline demonstration against the existing local Compose project."""

import json
import os
import subprocess
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx

CENTRAL_QUERY = r"""
import json, os
from sqlalchemy import create_engine, text
e=create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    result={}
    for n in (1,2,3):
        stock=c.execute(text("SELECT product_id,quantity FROM projected_stock "
                             "WHERE branch_id=:b ORDER BY product_id"),{"b":n}).all()
        total=c.execute(text("SELECT count(DISTINCT s.id),"
                             "coalesce(sum(l.quantity*l.unit_price_cents),0) FROM sales s "
                             "LEFT JOIN sale_lines l ON l.sale_id=s.id WHERE s.branch_id=:b"),
                        {"b":n}).one()
        result[str(n)]={"stock":{str(x.product_id):x.quantity for x in stock},
                        "count":total[0],"total_cents":total[1]}
    print(json.dumps(result))
"""
AUDIT_QUERY = r"""
import json, os
from sqlalchemy import create_engine, text
e=create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    result={}
    for n in (1,2,3):
        count=c.execute(text("SELECT count(*) FROM processed_events WHERE branch_id=:b"),
                        {"b":n}).scalar_one()
        cursor=c.execute(text("SELECT last_sequence FROM sync_cursors WHERE branch_id=:b"),
                         {"b":n}).scalar_one()
        result[str(n)]={"events":count,"cursor":cursor}
    print(json.dumps(result))
"""
OUTBOX_QUERY = r"""
import json, os, sys
from sqlalchemy import create_engine, text
ids=json.loads(sys.argv[1]); e=create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    events=[]
    for sale_id in ids:
        statement = text("SELECT id,branch_id,sequence,schema_version,event_type,payload "
                         "FROM outbox WHERE event_type='sale.created' ORDER BY sequence")
        rows=c.execute(statement).mappings()
        for row in rows:
            payload=json.loads(row["payload"]) if isinstance(row["payload"],str) else row["payload"]
            if payload.get("sale_id")==sale_id:
                events.append({"event_id":row["id"],"branch_id":row["branch_id"],"sequence":row["sequence"],"schema_version":row["schema_version"],"event_type":row["event_type"],"occurred_at":payload["occurred_at"],"payload":payload})
    print(json.dumps(events))
"""


class DemoError(RuntimeError):
    pass


class OfflineDemo:
    def __init__(self) -> None:
        self.root = Path(os.environ.get("DEMO_COMPOSE_DIR", Path(__file__).resolve().parents[2]))
        self.compose = ["docker", "compose"]
        if os.environ.get("DEMO_COMPOSE_FILE"):
            self.compose += ["-f", os.environ["DEMO_COMPOSE_FILE"]]
        self.central = os.getenv("DEMO_CENTRAL_URL", "http://127.0.0.1:8000")
        self.branches = {
            i: os.getenv(f"DEMO_BRANCH_{i}_URL", f"http://127.0.0.1:{8000 + i}") for i in (1, 2, 3)
        }
        self.timeout = float(os.getenv("DEMO_TIMEOUT_SECONDS", "90"))
        self.client = httpx.Client(timeout=5.0)
        self.run_id = uuid4().hex
        self.paused = False

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def wait(self, description: str, predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + self.timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (httpx.HTTPError, subprocess.SubprocessError, ValueError) as error:
                last_error = str(error)
            time.sleep(0.25)
        raise DemoError(f"Plazo agotado: {description}; {last_error}")

    def compose_run(self, *args: str) -> str:
        result = subprocess.run(
            [*self.compose, *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=45,
            check=True,
        )
        return result.stdout

    def central_state(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(self.compose_run("exec", "-T", "central", "python", "-c", CENTRAL_QUERY)),
        )

    def local_state(self, branch: int) -> dict[str, Any]:
        url = self.branches[branch]
        inventory = self.request("GET", url + "/inventory")
        sales = self.request("GET", url + "/sales")
        return {
            "stock": {str(row["product_id"]): row["quantity"] for row in inventory},
            "count": len(sales),
            "total_cents": sum(sale["total_cents"] for sale in sales),
        }

    def status(self, branch: int) -> dict[str, Any]:
        return cast(dict[str, Any], self.request("GET", self.branches[branch] + "/sync/status"))

    def synchronized(self, branch: int) -> bool:
        state = self.status(branch)
        return (
            not state["paused"]
            and not state["blocked"]
            and state["pending"] == 0
            and self.local_state(branch) == self.central_state()[str(branch)]
        )

    def sell(self, branch: int, index: int) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self.request(
                "POST",
                self.branches[branch] + "/sales",
                headers={"Idempotency-Key": f"demo-offline-{self.run_id}-{branch}-{index}"},
                json={"lines": [{"product_id": 1, "quantity": 1}]},
            ),
        )

    @staticmethod
    def check(condition: bool, message: str) -> None:
        if not condition:
            raise DemoError(message)

    def run(self) -> None:
        print(f"Ejecución {self.run_id}", flush=True)
        for branch in (1, 2, 3):
            self.wait(f"sucursal {branch} sincronizada", partial(self.synchronized, branch))
        baseline = {i: self.local_state(i) for i in (1, 2, 3)}
        for branch in (1, 2, 3):
            self.check(
                baseline[branch]["stock"].get("1", 0) >= (2 if branch == 2 else 1),
                f"Stock insuficiente en sucursal {branch}",
            )
        print("Antes:", json.dumps(baseline, ensure_ascii=False), flush=True)
        ids: dict[int, list[str]] = {1: [], 2: [], 3: []}
        try:
            # A lost pause response may still mean the server paused successfully.
            self.paused = True
            self.request("POST", self.branches[2] + "/demo/sync/pause")
            self.check(self.status(2)["paused"], "No se pudo pausar")
            # Synchronization was empty before pause, so no in-flight event can include these sales.
            sales2 = [self.sell(2, i) for i in range(2)]
            ids[2] = [sale["id"] for sale in sales2]
            during = self.local_state(2)
            central_during = self.central_state()["2"]
            self.check(
                during["stock"]["1"] == baseline[2]["stock"]["1"] - 2,
                "Stock local no bajó en dos",
            )
            self.check(during["count"] == baseline[2]["count"] + 2, "No se registraron dos ventas")
            self.check(central_during == baseline[2], "El central cambió durante la pausa")
            self.check(self.status(2)["pending"] == 2, "La outbox no tiene dos pendientes")
            print(
                "Durante:",
                json.dumps(
                    {"local_2": during, "central_2": central_during, "sync_2": self.status(2)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            other = {i: self.sell(i, 0) for i in (1, 3)}
            for i in (1, 3):
                ids[i] = [other[i]["id"]]
                self.wait(f"venta en sucursal {i}", partial(self.synchronized, i))
                actual = self.local_state(i)
                self.check(
                    actual["stock"]["1"] == baseline[i]["stock"]["1"] - 1
                    and actual["count"] == baseline[i]["count"] + 1
                    and actual["total_cents"]
                    == baseline[i]["total_cents"] + other[i]["total_cents"],
                    f"Diferencia inesperada en sucursal {i}",
                )
            self.compose_run("restart", "branch-2")
            self.wait(
                "reinicio de sucursal 2",
                lambda: (
                    self.request("GET", self.branches[2] + "/health/ready")["status"] == "ready"
                ),
            )
            self.check(
                self.status(2)["paused"] and self.status(2)["pending"] == 2,
                "La pausa o pendientes no sobrevivieron",
            )
            self.check(
                self.local_state(2) == during and self.central_state()["2"] == baseline[2],
                "El reinicio alteró datos",
            )
            persisted = {sale["id"] for sale in self.request("GET", self.branches[2] + "/sales")}
            self.check(set(ids[2]).issubset(persisted), "Ventas no sobrevivieron al reinicio")
            self.request("POST", self.branches[2] + "/demo/sync/resume")
            self.paused = False
            self.wait("convergencia de sucursal 2", lambda: self.synchronized(2))
            after = self.local_state(2)
            self.check(after == during, "El stock o ventas locales cambiaron al sincronizar")
            self.check(
                after["total_cents"]
                == baseline[2]["total_cents"] + sum(sale["total_cents"] for sale in sales2),
                "Total central incorrecto",
            )
            final = self.central_state()
            audit = self.compose_run("exec", "-T", "central", "python", "-c", AUDIT_QUERY)
            envelopes = []
            for branch in (1, 2, 3):
                events = json.loads(
                    self.compose_run(
                        "exec",
                        "-T",
                        f"branch-{branch}",
                        "python",
                        "-c",
                        OUTBOX_QUERY,
                        json.dumps(ids[branch]),
                    )
                )
                self.check(len(events) == len(ids[branch]), f"Faltan eventos de sucursal {branch}")
                envelopes.extend(events)
            replay = self.request("POST", self.central + "/sync/events", json={"events": envelopes})
            self.check(
                len(replay["results"]) == len(envelopes)
                and all(item["status"] == "duplicate" for item in replay["results"]),
                "El central no reconoció todos los duplicados",
            )
            self.check(self.central_state() == final, "El reenvío cambió los totales o existencias")
            self.check(
                self.compose_run("exec", "-T", "central", "python", "-c", AUDIT_QUERY) == audit,
                "El reenvío cambió el cursor o los eventos procesados",
            )
            print(
                "Después:",
                json.dumps(
                    {"central": final, "sync_2": self.status(2), "duplicados": len(envelopes)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            print("Demo completada: convergencia y deduplicación verificadas", flush=True)
        finally:
            if self.paused:
                try:
                    self.request("POST", self.branches[2] + "/demo/sync/resume")
                except httpx.HTTPError as error:
                    print(f"No se pudo reanudar sucursal 2: {error}", flush=True)
            self.client.close()


def run_demo() -> None:
    OfflineDemo().run()
