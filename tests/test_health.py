import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from retail_intelligence.branch.app import app as branch_app
from retail_intelligence.central.app import app as central_app
from retail_intelligence.shared.config import BranchSettings


def test_branch_health_and_persistent_database(monkeypatch, tmp_path):
    monkeypatch.setenv("BRANCH_ID", "2")
    db = tmp_path / "branch-2.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    with TestClient(branch_app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json() == {"status": "ready", "branch_id": "2"}
    assert db.exists()


def test_branch_rejects_invalid_configuration(monkeypatch):
    monkeypatch.setenv("BRANCH_ID", "4")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(ValidationError) as error:
        BranchSettings()
    assert {item["loc"][0] for item in error.value.errors()} == {"branch_id", "database_url"}


def test_central_readiness_reports_database_failure(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    with TestClient(central_app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
