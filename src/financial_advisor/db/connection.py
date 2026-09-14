"""The one place the database is opened.

Every query in the system goes through `connect()`. That is deliberate: PRD P4
defers SQLCipher to keep M0 moving, and the deferral is only cheap if adopting it
later is a single-file change. Funnelling access here is what preserves that.

It is also where the durable invariants are asserted on every connection —
foreign keys on (SQLite defaults them OFF, per-connection, which silently voids
every REFERENCES clause in schema.sql) and the schema version matching the code.

Migrations: schema.sql is version 1; each later version is one numbered file in
migrations/. A database behind the code is refused at connect time rather than
upgraded implicitly — upgrading rewrites tables holding your only copy of the data,
so it happens only via `fa init`, and only after a backup.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..money import Money
from ..paths import database_path, ensure_data_dir

__all__ = [
    "connect",
    "initialize",
    "migrate",
    "is_initialized",
    "schema_version_of",
    "MigrationResult",
    "SchemaVersionError",
    "SCHEMA_VERSION",
    "row_money",
]

SCHEMA_VERSION = 2
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")
_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class SchemaVersionError(RuntimeError):
    """The database schema doesn't match what this code expects."""


@dataclass
class MigrationResult:
    from_version: int
    applied: list[int] = field(default_factory=list)
    backup: Path | None = None


def _migrations() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise SchemaVersionError(f"unexpected file in migrations/: {path.name}")
        found.append((int(match.group(1)), path))
    versions = [v for v, _ in found]
    if versions != list(range(2, 2 + len(found))):
        raise SchemaVersionError(
            f"migrations must be numbered consecutively from 002; found {versions}"
        )
    if (versions[-1] if versions else 1) != SCHEMA_VERSION:
        raise SchemaVersionError("SCHEMA_VERSION does not match the newest migration file")
    return found


def _version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError as exc:
        raise SchemaVersionError(
            "not a financial-advisor database (no schema_version table)"
        ) from exc
    return int(row[0] or 0)


def schema_version_of(path: Path | None = None) -> int:
    conn = sqlite3.connect(path or database_path())
    try:
        return _version(conn)
    finally:
        conn.close()


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
        version = _version(conn)
        if version < SCHEMA_VERSION:
            raise SchemaVersionError(
                f"The database is at schema v{version}; this version of fa needs "
                f"v{SCHEMA_VERSION}. Run `fa init` to upgrade — a backup is taken first."
            )
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"The database is at schema v{version}, newer than this code "
                f"(v{SCHEMA_VERSION}). Update the code rather than downgrading the data."
            )
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
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
    """Create the database at the current schema, or upgrade an existing one. Idempotent."""
    db_path = path or database_path()
    if is_initialized(db_path):
        migrate(db_path)
        return db_path

    ensure_data_dir()
    schema = _SCHEMA_FILE.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    # Owner-only: part of how P4 is met alongside FileVault.
    db_path.chmod(0o600)
    migrate(db_path, backup=False)  # nothing to lose in a database created a moment ago
    return db_path


def migrate(path: Path | None = None, *, backup: bool = True) -> MigrationResult:
    """Apply pending migrations in order, each in its own transaction."""
    db_path = path or database_path()
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        current = _version(conn)
        result = MigrationResult(from_version=current)
        if current > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database is at schema v{current}, newer than this code (v{SCHEMA_VERSION})"
            )
        pending = [(v, p) for v, p in _migrations() if v > current]
        if not pending:
            return result
        if backup:
            result.backup = _backup(conn, db_path, current)
        for version, migration in pending:
            _apply(conn, version, migration)
            result.applied.append(version)
        return result
    finally:
        conn.close()


def _backup(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    # The backup API rather than a file copy: in WAL mode, committed data can sit in
    # the -wal file, and copying advisor.db alone would miss it.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db_path.with_name(f"{db_path.name}.v{version}-{stamp}.bak")
    destination = sqlite3.connect(target)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    target.chmod(0o600)
    return target


def _apply(conn: sqlite3.Connection, version: int, migration: Path) -> None:
    # SQLite's documented table-rebuild procedure: foreign keys off (which only takes
    # effect outside a transaction), rebuild inside one, check integrity before commit.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # executescript commits any open transaction first and adds no transaction
        # control of its own, so BEGIN goes in the script and COMMIT stays here —
        # after the checks, where a failure can still roll everything back.
        conn.executescript("BEGIN IMMEDIATE;\n" + migration.read_text())
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise SchemaVersionError(
                f"{migration.name} left {len(violations)} foreign-key violation(s); rolled back"
            )
        if _version(conn) != version:
            raise SchemaVersionError(f"{migration.name} did not record schema_version {version}")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def row_money(row: sqlite3.Row, key: str) -> Money:
    """Read a *_cents column back as Money.

    The only sanctioned way to cross the storage boundary — keeps the int/Money
    conversion in one reviewable place instead of scattered through call sites.
    """
    value = row[key]
    return Money.from_cents(0 if value is None else int(value))
