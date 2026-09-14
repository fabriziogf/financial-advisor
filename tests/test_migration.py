"""Schema migrations: refused implicitly, backed up, preserving, atomic."""

from __future__ import annotations

import sqlite3

import pytest

from financial_advisor.db import connection
from financial_advisor.db.connection import (
    _SCHEMA_FILE,
    SCHEMA_VERSION,
    SchemaVersionError,
    connect,
    initialize,
    migrate,
    schema_version_of,
)


def make_v1(path):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_FILE.read_text())
    conn.execute("INSERT INTO account (name, type_code) VALUES ('Old Brokerage', 'brokerage')")
    conn.execute(
        "INSERT INTO balance_snapshot (account_id, as_of_date, amount_cents) "
        "VALUES (1, '2026-01-02', 123456)"
    )
    conn.execute(
        "INSERT INTO security (symbol, expense_ratio, asset_class) VALUES ('VTI', '0.0003', '{}')"
    )
    conn.execute(
        "INSERT INTO position (account_id, security_id, quantity, market_value_cents, as_of_date) "
        "VALUES (1, 1, '10', NULL, '2026-01-02')"
    )
    conn.commit()
    conn.close()
    return path


def test_new_database_starts_at_the_current_version(tmp_path):
    path = initialize(tmp_path / "new.db")
    assert schema_version_of(path) == SCHEMA_VERSION
    assert not list(tmp_path.glob("*.bak"))  # nothing to back up in a brand-new database


def test_connect_refuses_an_outdated_schema(tmp_path):
    path = make_v1(tmp_path / "v1.db")
    with pytest.raises(SchemaVersionError, match="fa init"):
        with connect(path):
            pass


def test_migration_preserves_data_and_backs_up_first(tmp_path):
    path = make_v1(tmp_path / "v1.db")
    result = migrate(path)

    assert result.applied == [2]
    assert result.backup is not None and schema_version_of(result.backup) == 1
    assert schema_version_of(path) == 2
    with connect(path) as conn:
        assert conn.execute("SELECT amount_cents FROM balance_snapshot").fetchone()[0] == 123456
        # An unvalued position stays unvalued — never coerced to zero.
        row = tuple(
            conn.execute("SELECT quantity, market_value_cents, source FROM position").fetchone()
        )
        assert row == ("10", None, "manual")
        columns = [r[1] for r in conn.execute("PRAGMA table_info(security)")]
        assert columns == ["id", "symbol", "name", "updated_at"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_is_idempotent(tmp_path):
    path = make_v1(tmp_path / "v1.db")
    migrate(path)
    again = migrate(path)
    assert again.applied == [] and again.backup is None


def test_a_failing_migration_rolls_back_completely(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "002_broken.sql").write_text(
        "CREATE TABLE half_done (x INTEGER);\nINSERT INTO no_such_table VALUES (1);\n"
    )
    monkeypatch.setattr(connection, "_MIGRATIONS_DIR", migrations)
    path = make_v1(tmp_path / "v1.db")

    with pytest.raises(sqlite3.OperationalError):
        migrate(path)

    assert schema_version_of(path) == 1
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "half_done" not in tables
