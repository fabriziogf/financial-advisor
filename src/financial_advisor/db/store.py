"""Reads and writes over the schema. Thin on purpose — no analysis lives here."""

from __future__ import annotations

import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..money import Money
from ..rules import normalize_symbol

__all__ = [
    "Account",
    "Terms",
    "Holding",
    "PositionRow",
    "upsert_institution",
    "create_account",
    "find_account",
    "list_accounts",
    "set_balance",
    "latest_balances",
    "get_terms",
    "set_terms",
    "upsert_security",
    "replace_positions",
    "latest_positions",
    "transactions_between",
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
    is_investment: bool = False


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
           t.label AS type_label, t.is_liability, t.is_investment,
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
        is_investment=bool(row["is_investment"]),
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


# --- Account terms (F1.6) -------------------------------------------------
@dataclass(frozen=True)
class Terms:
    account_id: int
    rate: Decimal | None              # fraction: APY for deposits, APR for debts
    rate_kind: str | None             # "fixed" | "variable"
    minimum_payment: Money | None
    promo_ends_on: date | None
    post_promo_rate: Decimal | None
    revolving: bool | None            # credit cards: does the balance carry over?
    updated_on: date


_TERMS_FIELDS = ("rate", "rate_kind", "minimum_payment", "promo_ends_on", "post_promo_rate", "revolving")


def get_terms(conn: sqlite3.Connection) -> dict[int, Terms]:
    terms: dict[int, Terms] = {}
    for row in conn.execute("SELECT * FROM account_terms"):
        terms[int(row["account_id"])] = Terms(
            account_id=int(row["account_id"]),
            rate=Decimal(row["rate"]) if row["rate"] is not None else None,
            rate_kind=row["rate_kind"],
            minimum_payment=(
                Money.from_cents(int(row["minimum_payment_cents"]))
                if row["minimum_payment_cents"] is not None
                else None
            ),
            promo_ends_on=date.fromisoformat(row["promo_ends_on"]) if row["promo_ends_on"] else None,
            post_promo_rate=Decimal(row["post_promo_rate"]) if row["post_promo_rate"] is not None else None,
            revolving=None if row["revolving"] is None else bool(row["revolving"]),
            updated_on=date.fromisoformat(row["updated_on"]),
        )
    return terms


def _check_rate(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal fraction, got {type(value).__name__}")
    if not Decimal(0) <= value <= Decimal(1):
        raise ValueError(
            f"{name} {value} is outside 0–100%. Rates are stored as fractions: 0.0425 for 4.25%."
        )


def set_terms(conn: sqlite3.Connection, account_id: int, *, updated_on: date, **changes: object) -> Terms:
    """Update the given fields, keeping the rest. Passing None clears a field."""
    unknown = set(changes) - set(_TERMS_FIELDS)
    if unknown:
        raise TypeError(f"unknown terms field(s): {sorted(unknown)}")

    current = get_terms(conn).get(account_id)
    values: dict[str, object] = {
        name: (getattr(current, name) if current else None) for name in _TERMS_FIELDS
    }
    values.update(changes)

    _check_rate(values["rate"], "rate")  # type: ignore[arg-type]
    _check_rate(values["post_promo_rate"], "post_promo_rate")  # type: ignore[arg-type]
    if values["rate_kind"] not in (None, "fixed", "variable"):
        raise ValueError("rate_kind must be 'fixed' or 'variable'")
    minimum = values["minimum_payment"]
    if minimum is not None and (not isinstance(minimum, Money) or minimum.is_negative()):
        raise ValueError("minimum_payment must be a non-negative Money amount")
    if values["revolving"] not in (None, True, False):
        raise ValueError("revolving must be true, false, or None")

    conn.execute(
        """INSERT INTO account_terms
                  (account_id, rate, rate_kind, minimum_payment_cents, promo_ends_on,
                   post_promo_rate, revolving, updated_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET
               rate = excluded.rate,
               rate_kind = excluded.rate_kind,
               minimum_payment_cents = excluded.minimum_payment_cents,
               promo_ends_on = excluded.promo_ends_on,
               post_promo_rate = excluded.post_promo_rate,
               revolving = excluded.revolving,
               updated_on = excluded.updated_on""",
        (
            account_id,
            str(values["rate"]) if values["rate"] is not None else None,
            values["rate_kind"],
            minimum.cents if isinstance(minimum, Money) else None,
            values["promo_ends_on"].isoformat() if values["promo_ends_on"] else None,  # type: ignore[union-attr]
            str(values["post_promo_rate"]) if values["post_promo_rate"] is not None else None,
            None if values["revolving"] is None else int(bool(values["revolving"])),
            updated_on.isoformat(),
        ),
    )
    return get_terms(conn)[account_id]


# --- Securities and positions ---------------------------------------------
@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str | None
    quantity: Decimal | None
    market_value: Money | None


@dataclass(frozen=True)
class PositionRow:
    account_id: int
    symbol: str
    quantity: Decimal | None
    market_value: Money | None
    as_of: date


def upsert_security(conn: sqlite3.Connection, symbol: str, name: str | None = None) -> int:
    symbol = normalize_symbol(symbol)
    conn.execute(
        """INSERT INTO security (symbol, name) VALUES (?, ?)
           ON CONFLICT(symbol) DO UPDATE SET name = COALESCE(security.name, excluded.name)""",
        (symbol, name),
    )
    return int(conn.execute("SELECT id FROM security WHERE symbol = ?", (symbol,)).fetchone()["id"])


def replace_positions(
    conn: sqlite3.Connection,
    account_id: int,
    as_of: date,
    holdings: list[Holding],
    *,
    source: str = "import",
    import_run_id: int | None = None,
) -> int:
    """Record an account's complete holdings on a date. Returns positions written.

    Replaces rather than merges: a holdings export is the whole account on that day,
    so a position sold since the previous export must disappear, not linger as a
    phantom holding that inflates every allocation figure.

    The same symbol appearing on several lines (tax lots) is combined. A value
    missing from any lot leaves the combined value unknown rather than partial.
    """
    combined: OrderedDict[str, Holding] = OrderedDict()
    for holding in holdings:
        symbol = normalize_symbol(holding.symbol)
        prior = combined.get(symbol)
        if prior is None:
            combined[symbol] = Holding(symbol, holding.name, holding.quantity, holding.market_value)
            continue
        quantity = (
            prior.quantity + holding.quantity
            if prior.quantity is not None and holding.quantity is not None
            else None
        )
        value = (
            prior.market_value + holding.market_value
            if prior.market_value is not None and holding.market_value is not None
            else None
        )
        combined[symbol] = Holding(symbol, prior.name or holding.name, quantity, value)

    conn.execute(
        "DELETE FROM position WHERE account_id = ? AND as_of_date = ?", (account_id, as_of.isoformat())
    )
    for holding in combined.values():
        security_id = upsert_security(conn, holding.symbol, holding.name)
        conn.execute(
            """INSERT INTO position (account_id, security_id, quantity, market_value_cents,
                                     as_of_date, source, import_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id,
                security_id,
                str(holding.quantity) if holding.quantity is not None else None,
                holding.market_value.cents if holding.market_value is not None else None,
                as_of.isoformat(),
                source,
                import_run_id,
            ),
        )
    return len(combined)


def latest_positions(conn: sqlite3.Connection, as_of: date) -> dict[int, list[PositionRow]]:
    """Each account's most recent holdings at or before `as_of`."""
    rows = conn.execute(
        """SELECT p.account_id, s.symbol, p.quantity, p.market_value_cents, p.as_of_date
             FROM position p
             JOIN security s ON s.id = p.security_id
             JOIN (SELECT account_id, MAX(as_of_date) AS newest
                     FROM position
                    WHERE as_of_date <= ?
                    GROUP BY account_id) latest
               ON latest.account_id = p.account_id AND latest.newest = p.as_of_date
            ORDER BY p.account_id, s.symbol""",
        (as_of.isoformat(),),
    ).fetchall()
    result: dict[int, list[PositionRow]] = {}
    for row in rows:
        result.setdefault(int(row["account_id"]), []).append(
            PositionRow(
                account_id=int(row["account_id"]),
                symbol=row["symbol"],
                quantity=Decimal(row["quantity"]) if row["quantity"] is not None else None,
                market_value=(
                    Money.from_cents(int(row["market_value_cents"]))
                    if row["market_value_cents"] is not None
                    else None
                ),
                as_of=date.fromisoformat(row["as_of_date"]),
            )
        )
    return result


# --- Transactions ---------------------------------------------------------
def transactions_between(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT x.id, x.account_id, a.type_code, x.posted_on, x.amount_cents, x.description
             FROM txn x
             JOIN account a ON a.id = x.account_id
            WHERE a.is_active = 1 AND x.posted_on BETWEEN ? AND ?
            ORDER BY x.posted_on, x.id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
