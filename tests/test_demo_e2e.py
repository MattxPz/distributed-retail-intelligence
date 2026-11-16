"""Opt-in, isolated Docker e2e: offline demo plus actual HTTP transport fault."""

import os
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEMO_E2E") != "1", reason="Set RUN_DEMO_E2E=1 for Docker e2e"
)
ROOT = Path(__file__).resolve().parents[1]
PROXY = """
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
class Proxy(BaseHTTPRequestHandler):
    blocked = False
    def do_POST(self):
        if self.path in ('/off', '/on'):
            Proxy.blocked = self.path == '/off'
            self.send_response(200); self.end_headers(); return
        data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if Proxy.blocked:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        try:
            req = Request('http://central:8000' + self.path, data=data, method='POST',
                          headers={'Content-Type':'application/json'})
            with urlopen(req, timeout=5) as result:
                body = result.read(); self.send_response(result.status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers(); self.wfile.write(body)
        except (HTTPError, URLError):
            self.send_response(502); self.end_headers()
ThreadingHTTPServer(('0.0.0.0', 8000), Proxy).serve_forever()
"""


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def poll(predicate, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (httpx.HTTPError, KeyError, IndexError):
            pass
        time.sleep(0.2)
    pytest.fail("Timed out waiting for the expected state")


def test_demo_and_network_fault(tmp_path):
    project = "dri-demo-test-" + uuid4().hex[:12]
    repo = ROOT.as_posix()
    temp = tmp_path.as_posix()
    ports = [port() for _ in range(5)]
    (tmp_path / "proxy.py").write_text(PROXY, encoding="utf-8")
    config = f'''
services:
  postgres:
    image: postgres:17
    environment: {{POSTGRES_USER: test, POSTGRES_PASSWORD: test_only, POSTGRES_DB: test}}
    tmpfs: ["/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test -d test"]
      interval: 2s
      timeout: 2s
      retries: 30
  central:
    image: distributed-retail-intelligence-tests:0.1.0
    command: uvicorn retail_intelligence.central.app:app --host 0.0.0.0 --port 8000
    environment: {{PYTHONPATH: /workspace/src, DATABASE_URL: "postgresql+psycopg://test:test_only@postgres:5432/test"}}
    volumes: ["{repo}:/workspace:ro"]
    depends_on:
      postgres: {{condition: service_healthy}}
    ports: ["127.0.0.1:{ports[0]}:8000"]
  proxy:
    image: distributed-retail-intelligence-tests:0.1.0
    command: python /test/proxy.py
    volumes: ["{temp}:/test:ro"]
    ports: ["127.0.0.1:{ports[4]}:8000"]
'''
    for i in (1, 2, 3):
        upstream = "http://proxy:8000" if i == 2 else "http://central:8000"
        config += f'''
  branch-{i}:
    image: distributed-retail-intelligence-tests:0.1.0
    command: uvicorn retail_intelligence.branch.app:app --host 0.0.0.0 --port 8000 --workers 1
    environment:
      PYTHONPATH: /workspace/src
      DATABASE_URL: sqlite:////data/branch.sqlite3
      BRANCH_ID: "{i}"
      CENTRAL_URL: "{upstream}"
      DEMO_SYNC_ENABLED: "true"
    volumes: ["{repo}:/workspace:ro", "branch-{i}-data:/data"]
    ports: ["127.0.0.1:{ports[i]}:8000"]
'''
    config += "\nvolumes:\n  branch-1-data:\n  branch-2-data:\n  branch-3-data:\n"
    path = tmp_path / "compose.yaml"
    path.write_text(config, encoding="utf-8")
    compose = ["docker", "compose", "-p", project, "-f", str(path)]
    env = dict(
        os.environ,
        DEMO_COMPOSE_FILE=str(path),
        DEMO_CENTRAL_URL=f"http://127.0.0.1:{ports[0]}",
        DEMO_TIMEOUT_SECONDS="90",
    )
    env.update({f"DEMO_BRANCH_{i}_URL": f"http://127.0.0.1:{ports[i]}" for i in (1, 2, 3)})
    env["COMPOSE_PROJECT_NAME"] = project
    try:
        subprocess.run([*compose, "up", "-d", "--no-build"], check=True, timeout=90)
        central, branch, proxy = [f"http://127.0.0.1:{ports[i]}" for i in (0, 2, 4)]
        with httpx.Client(timeout=5) as client:
            poll(lambda: client.get(branch + "/health/ready").status_code == 200)
            poll(lambda: client.get(central + "/health/ready").status_code == 200)
            poll(lambda: client.get(branch + "/sync/status").json()["pending"] == 0)
            command = ["uv", "run", "python", "-m", "retail_intelligence.cli", "demo-offline"]
            first = subprocess.run(
                command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=240
            )
            assert first.returncode == 0, first.stdout + first.stderr
            assert (
                "Antes:" in first.stdout
                and "Durante:" in first.stdout
                and "Después:" in first.stdout
            )
            assert "duplicados" in first.stdout
            print(first.stdout)

            # A real proxy refuses HTTP while the branch and SQLite remain available.
            def central_values():
                query = (
                    "SELECT (SELECT quantity FROM projected_stock "
                    "WHERE branch_id=2 AND product_id=1), "
                    "(SELECT count(*) FROM sales WHERE branch_id=2), "
                    "(SELECT coalesce(sum(l.quantity*l.unit_price_cents),0) FROM sales s "
                    "JOIN sale_lines l ON l.sale_id=s.id WHERE s.branch_id=2)"
                )
                result = subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "-U",
                        "test",
                        "-d",
                        "test",
                        "-Atc",
                        query,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                return tuple(map(int, result.stdout.strip().split("|")))

            baseline = central_values()
            assert client.post(proxy + "/off").status_code == 200
            before = client.get(branch + "/inventory").json()[0]["quantity"]
            cursor = client.get(central + "/sync/status").json()[1]["last_sequence"]
            sale = client.post(
                branch + "/sales",
                headers={"Idempotency-Key": "network-" + uuid4().hex},
                json={"lines": [{"product_id": 1, "quantity": 1}]},
            )
            assert sale.status_code == 200
            poll(lambda: client.get(branch + "/sync/status").json()["last_error"] is not None)
            assert "RemoteProtocolError" in client.get(branch + "/sync/status").json()["last_error"]
            assert client.get(branch + "/health/ready").status_code == 200
            assert client.get(branch + "/inventory").json()[0]["quantity"] == before - 1
            assert client.get(branch + "/sync/status").json()["pending"] == 1
            assert client.get(central + "/sync/status").json()[1]["last_sequence"] == cursor
            assert central_values() == baseline
            assert client.post(proxy + "/on").status_code == 200
            poll(lambda: client.get(branch + "/sync/status").json()["pending"] == 0)
            assert client.get(central + "/sync/status").json()[1]["last_sequence"] == cursor + 1
            assert central_values() == (
                baseline[0] - 1,
                baseline[1] + 1,
                baseline[2] + sale.json()["total_cents"],
            )
            # Repeat the full demo over existing, nonempty state.
            second = subprocess.run(
                command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=240
            )
            assert second.returncode == 0, second.stdout + second.stderr
            print(second.stdout)
            print("HTTP proxy fault: branch stayed live; one pending sale converged after recovery")
    finally:
        # Project name is generated for this test and used for every command above.
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], check=True, timeout=90)
