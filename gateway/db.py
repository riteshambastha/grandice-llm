import asyncio
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    license_tier  TEXT NOT NULL DEFAULT 'free',
    member_limit  INTEGER NOT NULL DEFAULT 5,
    rpm_limit     INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    rpm_limit   INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (org_id, email),
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS api_keys (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash       TEXT NOT NULL UNIQUE,
    key_prefix     TEXT NOT NULL,
    name           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_used_at   TEXT,
    revoked        INTEGER NOT NULL DEFAULT 0,
    rpm_limit      INTEGER,
    allowed_models TEXT,
    org_id         INTEGER,
    member_id      INTEGER,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE SET NULL,
    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    key_id            INTEGER,
    app_name          TEXT,
    model             TEXT,
    endpoint          TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    duration_ms       INTEGER,
    status            INTEGER,
    org_id             INTEGER,
    member_id          INTEGER,
    org_name           TEXT,
    member_name        TEXT
);

CREATE TABLE IF NOT EXISTS contact_submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    question     TEXT NOT NULL,
    email_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id          TEXT NOT NULL UNIQUE,
    ts                  TEXT NOT NULL,
    key_id              INTEGER,
    org_id              INTEGER,
    member_id           INTEGER,
    endpoint            TEXT NOT NULL,
    agent               TEXT,
    privacy_mode        TEXT NOT NULL,
    status              INTEGER NOT NULL,
    duration_ms         INTEGER NOT NULL,
    input_schema        TEXT NOT NULL,
    output_schema       TEXT,
    source_count        INTEGER NOT NULL DEFAULT 0,
    warning_count       INTEGER NOT NULL DEFAULT 0,
    methodology_version TEXT,
    FOREIGN KEY (key_id) REFERENCES api_keys(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log (ts);
CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_log (key_id);
CREATE INDEX IF NOT EXISTS idx_contact_created ON contact_submissions (created_at);
CREATE INDEX IF NOT EXISTS idx_members_org ON members (org_id);
CREATE INDEX IF NOT EXISTS idx_domain_runs_ts ON domain_runs (ts);
CREATE INDEX IF NOT EXISTS idx_domain_runs_org ON domain_runs (org_id);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL lets the request path write usage records while admin queries read.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "api_keys", "org_id", "INTEGER")
        _ensure_column(conn, "api_keys", "member_id", "INTEGER")
        _ensure_column(conn, "usage_log", "org_id", "INTEGER")
        _ensure_column(conn, "usage_log", "member_id", "INTEGER")
        _ensure_column(conn, "usage_log", "org_name", "TEXT")
        _ensure_column(conn, "usage_log", "member_name", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_keys_org ON api_keys (org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_keys_member ON api_keys (member_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_org ON usage_log (org_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_member ON usage_log (member_id)")
        conn.commit()


def _run(sql: str, params: Iterable[Any], *, fetch: str | None) -> Any:
    with _connect(get_settings().database_path) as conn:
        cur = conn.execute(sql, tuple(params))
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        conn.commit()
        return cur.lastrowid


async def fetch_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return await asyncio.to_thread(_run, sql, params, fetch="one")


async def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return await asyncio.to_thread(_run, sql, params, fetch="all")


async def execute(sql: str, params: Iterable[Any] = ()) -> int:
    return await asyncio.to_thread(_run, sql, params, fetch=None)
