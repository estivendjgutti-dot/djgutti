"""Acesso SQLite e migrações do servidor de automações.

Regras:
- Um único escritor (o worker). Scripts de status/backup apenas leem.
- Horários sempre gravados em UTC, formato ISO 8601 com sufixo Z.
- journal_mode configurável (WAL por padrão; DELETE se WAL falhar no PRoot).
"""

import datetime as _dt
import os
import sqlite3

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_db_path():
    return os.environ.get("E4T_DB_PATH") or os.path.join(PROJECT_DIR, "data", "app.db")


def utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def to_iso(dt):
    """datetime aware -> '2026-10-17T18:40:00Z' (sem microssegundos)."""
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text):
    if not text:
        return None
    return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def from_epoch(ts):
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)


MIGRATIONS = [
    # v1: fila durável, histórico, estado e tarefa demonstrativa.
    """
    CREATE TABLE jobs (
        job_id                  TEXT PRIMARY KEY,
        kind                    TEXT NOT NULL,
        enabled                 INTEGER NOT NULL DEFAULT 0,
        interval_seconds        INTEGER NOT NULL,
        catchup                 TEXT NOT NULL DEFAULT 'latest',
        catchup_window_seconds  INTEGER NOT NULL DEFAULT 0,
        max_lateness_seconds    INTEGER,
        max_attempts            INTEGER NOT NULL DEFAULT 5,
        timeout_seconds         INTEGER NOT NULL DEFAULT 30,
        external_effect         INTEGER NOT NULL DEFAULT 0,
        params_json             TEXT NOT NULL DEFAULT '{}',
        last_slot_utc           TEXT,
        updated_at_utc          TEXT NOT NULL
    );

    CREATE TABLE runs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id            TEXT NOT NULL REFERENCES jobs(job_id),
        scheduled_at_utc  TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','running','succeeded','failed',
                                            'dead','needs_review','skipped')),
        attempts          INTEGER NOT NULL DEFAULT 0,
        next_retry_at     TEXT,
        started_at        TEXT,
        finished_at       TEXT,
        lease_expires_at  TEXT,
        idempotency_key   TEXT NOT NULL,
        result            TEXT,
        error             TEXT,
        created_at_utc    TEXT NOT NULL,
        UNIQUE (job_id, scheduled_at_utc)
    );
    CREATE INDEX runs_status_due ON runs(status, next_retry_at, scheduled_at_utc);

    CREATE TABLE state (
        key             TEXT PRIMARY KEY,
        value           TEXT,
        updated_at_utc  TEXT NOT NULL
    );

    CREATE TABLE demo_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id            INTEGER NOT NULL UNIQUE REFERENCES runs(id),
        job_id            TEXT NOT NULL,
        scheduled_at_utc  TEXT NOT NULL,
        recorded_at_utc   TEXT NOT NULL
    );
    """,
]


def connect(path=None, readonly=False):
    path = path or default_db_path()
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    else:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        _set_journal_mode(conn)
        conn.execute("PRAGMA synchronous = NORMAL" if _journal(conn) == "wal" else "PRAGMA synchronous = FULL")
    return conn


def _journal(conn):
    return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()


def _set_journal_mode(conn):
    wanted = (os.environ.get("E4T_SQLITE_JOURNAL") or "wal").lower()
    if wanted not in ("wal", "delete", "truncate"):
        wanted = "wal"
    try:
        got = conn.execute("PRAGMA journal_mode = %s" % wanted).fetchone()[0].lower()
    except sqlite3.DatabaseError:
        got = None
    if got != wanted and wanted == "wal":
        # PRoot/sistemas de arquivos sem mmap compartilhado: journal tradicional.
        conn.execute("PRAGMA journal_mode = DELETE")


def migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx in range(version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in MIGRATIONS[idx].split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            conn.execute("PRAGMA user_version = %d" % (idx + 1))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO state(key, value, updated_at_utc) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at_utc = excluded.updated_at_utc",
        (key, value, to_iso(utcnow())),
    )


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default
