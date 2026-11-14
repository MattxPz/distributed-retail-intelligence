import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from retail_intelligence.shared.health import database_engine


def test_sqlite_enforces_foreign_keys_and_configures_timeout(tmp_path):
    path = tmp_path / "constraints.sqlite3"
    with database_engine(f"sqlite:///{path.as_posix()}") as engine:
        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA busy_timeout")) == 5000
            connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))"))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("INSERT INTO child VALUES (1)"))
