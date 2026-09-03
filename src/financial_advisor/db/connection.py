"""The one place the database is opened.

Every query in the system goes through `connect()`. That is deliberate: PRD P4
defers SQLCipher to keep M0 moving, and the deferral is only cheap if adopting it
later is a single-file change. Funnelling access here is what preserves that.

It is also where the durable invariants are asserted on every connection —
foreign keys on (SQLite defaults them OFF, per-connection, which silently voids
every REFERENCES clause in schema.sql).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..money import Money
from ..paths import database_path, ensure_data_dir

__all__ = ["connect", "initialize", "is_initialized", "SCHEMA_VERSION", "row_money"]

SCHEMA_VERSION = 1
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # Off by default and per-connection. Without this every FK in the schema is
    # decorative, and orphaned rows accumulate silently.
    conn.execute("PRAGMA foreign_keys = ON")
    # Concurrent CLI invocations shouldn't fail on a locked database.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Durability over speed: this is a ledger, not a cache.
    conn.execute("PRAGMA synchronous = FULL")


@contextmanager
def connect(path: Path | None = None, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    """Open the database. Commits on clean exit, rolls back on exception.

    The rollback matters for imports: a file that fails halfway must leave no
    partial rows, or the next run's dedupe check compares against a corrupt
    baseline and the damage compounds.
    """
    db_path = path or database_path()
    if not create and not db_path.exists():
        raise FileNotFoundError(
            f"No database at {db_path}. Run `fa init` first."
        )
    if create:
        ensure_data_dir()

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        _configure(conn)
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def is_initialized(path: Path | None = None) -> bool:
    db_path = path or database_path()
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def initialize(path: Path | None = None) -> Path:
    """Create the database and apply the schema. Idempotent."""
    db_path = path or database_path()
    if is_initialized(db_path):
        return db_path

    ensure_data_dir()
    schema = _SCHEMA_FILE.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    # Owner-only: part of how P4 is met for M0 alongside FileVault.
    db_path.chmod(0o600)
    return db_path


def row_money(row: sqlite3.Row, key: str) -> Money:
    """Read a *_cents column back as Money.

    The only sanctioned way to cross the storage boundary — keeps the int/Money
    conversion in one reviewable place instead of scattered through call sites.
    """
    value = row[key]
    return Money.from_cents(0 if value is None else int(value))
