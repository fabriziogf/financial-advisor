"""Reads and writes over the schema. Thin on purpose — no analysis lives here."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..money import Money

__all__ = [
    "Account",
    "upsert_institution",
    "create_account",
    "find_account",
    "list_accounts",
    "set_balance",
    "latest_balances",
]


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    type_code: str
    type_label: str
    is_liability: bool
    is_manual: bool
    institution: str | None
    tax_treatment: str


def upsert_institution(conn: sqlite3.Connection, name: str) -> int:
    slug = "-".join(name.lower().split())
    conn.execute(
        "INSERT INTO institution (name, slug) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
        (name, slug),
    )
    row = conn.execute("SELECT id FROM institution WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def create_account(
    conn: sqlite3.Connection,
    *,
    name: str,
    type_code: str,
    institution: str | None = None,
    tax_treatment: str = "taxable",
    is_manual: bool = False,
) -> int:
    known = conn.execute(
        "SELECT 1 FROM account_type WHERE code = ?", (type_code,)
    ).fetchone()
    if not known:
        valid = [
            r["code"]
            for r in conn.execute("SELECT code FROM account_type ORDER BY sort_order")
        ]
        raise ValueError(f"unknown account type {type_code!r}. Valid: {', '.join(valid)}")

    institution_id = upsert_institution(conn, institution) if institution else None
    cur = conn.execute(
        """INSERT INTO account (institution_id, name, type_code, tax_treatment, is_manual)
           VALUES (?, ?, ?, ?, ?)""",
        (institution_id, name, type_code, tax_treatment, int(is_manual)),
    )
    return int(cur.lastrowid)


_ACCOUNT_SELECT = """
    SELECT a.id, a.name, a.type_code, a.tax_treatment, a.is_manual,
           t.label AS type_label, t.is_liability,
           i.name AS institution
      FROM account a
      JOIN account_type t ON t.code = a.type_code
      LEFT JOIN institution i ON i.id = a.institution_id
"""


def _to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=int(row["id"]),
        name=row["name"],
        type_code=row["type_code"],
        type_label=row["type_label"],
        is_liability=bool(row["is_liability"]),
        is_manual=bool(row["is_manual"]),
        institution=row["institution"],
        tax_treatment=row["tax_treatment"],
    )


def find_account(conn: sqlite3.Connection, name: str) -> Account | None:
    row = conn.execute(_ACCOUNT_SELECT + " WHERE a.name = ?", (name,)).fetchone()
    return _to_account(row) if row else None


def list_accounts(conn: sqlite3.Connection, *, active_only: bool = True) -> list[Account]:
    sql = _ACCOUNT_SELECT
    if active_only:
        sql += " WHERE a.is_active = 1"
    sql += " ORDER BY t.sort_order, a.name"
    return [_to_account(r) for r in conn.execute(sql)]


def set_balance(
    conn: sqlite3.Connection,
    account_id: int,
    amount: Money,
    as_of: date,
    *,
    source: str = "manual",
    import_run_id: int | None = None,
) -> None:
    """Record a balance. One per account per day — a re-import replaces."""
    conn.execute(
        """INSERT INTO balance_snapshot
                  (account_id, as_of_date, amount_cents, source, import_run_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(account_id, as_of_date)
           DO UPDATE SET amount_cents = excluded.amount_cents,
                         source       = excluded.source,
                         import_run_id = excluded.import_run_id""",
        (account_id, as_of.isoformat(), amount.cents, source, import_run_id),
    )


def latest_balances(conn: sqlite3.Connection, as_of: date | None = None) -> dict[int, sqlite3.Row]:
    """Most recent balance per account, at or before `as_of`.

    Returns the row rather than just the amount so callers can see the date — net
    worth needs it to flag stale figures instead of presenting them as current.
    """
    cutoff = (as_of or date.today()).isoformat()
    rows = conn.execute(
        """SELECT b.account_id, b.amount_cents, b.as_of_date, b.source
             FROM balance_snapshot b
             JOIN (SELECT account_id, MAX(as_of_date) AS newest
                     FROM balance_snapshot
                    WHERE as_of_date <= ?
                    GROUP BY account_id) latest
               ON latest.account_id = b.account_id
              AND latest.newest = b.as_of_date""",
        (cutoff,),
    ).fetchall()
    return {int(r["account_id"]): r for r in rows}
