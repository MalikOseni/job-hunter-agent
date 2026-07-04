from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_db_path() -> Path:
    env_override = os.environ.get("JOB_HUNTER_DB_PATH")
    if env_override:
        return Path(env_override).expanduser()
    return project_root() / "data" / "job_hunter.db"


def resolve_db_path(cli_db_path: Path | None = None) -> Path:
    if cli_db_path is not None:
        return cli_db_path.expanduser()
    return default_db_path()


def schema_path() -> Path:
    return project_root() / "sql" / "001_init.sql"


def connect_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(conn: sqlite3.Connection) -> None:
    schema_sql = schema_path().read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


@contextmanager
def open_database(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect_database(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
