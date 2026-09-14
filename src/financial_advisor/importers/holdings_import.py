"""Holdings (positions) CSV import — feeds allocation, location, fees, and concentration.

Columns are detected from the header rather than configured per brokerage, for the
same privacy reason as the transaction importer.

A holdings export is the whole account on one day, so importing it replaces that
day's positions rather than merging (see store.replace_positions). Rows that carry
money but aren't a recognizable holding — "Pending Activity", footers — are skipped
and *listed*, never silently dropped: excluded money that nobody mentions is how a
portfolio total ends up quietly wrong.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..db.store import Holding, replace_positions, set_balance
from ..money import Money, MoneyParseError, parse_currency_code
from ..rules import normalize_symbol
from .csv_import import _CURRENCY_HEADERS, ImportError_, _norm_header, _read_text

__all__ = ["HoldingsResult", "parse_holdings", "import_holdings"]

_SYMBOL = {"symbol", "ticker", "ticker symbol", "security symbol", "sym", "symbol cusip"}
_VALUE = {
    "current value", "market value", "value", "total value", "mkt value", "marketvalue",
    "ending value", "position value", "value usd",
}
_QUANTITY = {"quantity", "shares", "qty", "units", "share quantity", "shares held"}
_NAME = {
    "description", "security description", "name", "security name", "investment name",
    "fund name", "investment",
}
_ACCOUNT = {"account", "account name", "account number", "account id"}

# Tickers and CUSIPs: no spaces, short. "Pending Activity" and disclaimer text fail this.
_SYMBOL_SHAPE = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,11}$")


@dataclass
class _Columns:
    symbol: str
    value: str
    quantity: str | None = None
    name: str | None = None
    currency: str | None = None
    account: str | None = None


@dataclass
class HoldingsResult:
    account: str
    as_of: date
    holdings: list[Holding] = field(default_factory=list)
    positions_written: int = 0
    total: Money | None = None
    skipped_file: bool = False
    skipped_rows: list[str] = field(default_factory=list)
    balance_updated: bool = False


def _detect(headers: list[str]) -> _Columns:
    found: dict[str, str] = {}
    for raw in headers:
        norm = _norm_header(raw)
        for role, vocabulary in (
            ("symbol", _SYMBOL),
            ("value", _VALUE),
            ("quantity", _QUANTITY),
            ("name", _NAME),
            ("currency", _CURRENCY_HEADERS),
            ("account", _ACCOUNT),
        ):
            if norm in vocabulary and role not in found:
                found[role] = raw
                break
    missing = [label for role, label in (("symbol", "a symbol column"), ("value", "a market value column")) if role not in found]
    if missing:
        raise ImportError_(f"Could not identify {' or '.join(missing)}.\n  Headers seen: {headers}")
    return _Columns(**found)


def _header_row(lines: list[str]) -> int:
    for index, line in enumerate(lines[:15]):
        norm = {_norm_header(cell) for cell in next(csv.reader([line]), [])}
        if norm & _SYMBOL and norm & _VALUE:
            return index
    raise ImportError_(
        "No header row with both a symbol and a market value column in the first 15 lines. "
        "Is this a holdings (positions) export rather than a transaction history?"
    )


def _cell(record: dict, column: str | None) -> str:
    if column is None:
        return ""
    value = record.get(column)
    return value.strip() if isinstance(value, str) else ""


def parse_holdings(text: str, *, only: str | None = None) -> tuple[list[Holding], list[str]]:
    """Holdings and a list of skipped rows, each described with its line and amount."""
    lines = text.splitlines()
    if not lines:
        raise ImportError_("file is empty")
    start = _header_row(lines)
    body = "\n".join(lines[start:])
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(body), dialect=dialect)
    columns = _detect([h for h in (reader.fieldnames or []) if h is not None])
    numbered = list(enumerate(reader, start=start + 2))

    if columns.account:
        if only:
            numbered = [(n, r) for n, r in numbered if only in _cell(r, columns.account)]
            if not numbered:
                raise ImportError_(f"No rows match --only {only!r} in the {columns.account!r} column.")
        else:
            distinct = {_cell(r, columns.account) for _, r in numbered if _cell(r, columns.account)}
            if len(distinct) > 1:
                # Count only: echoing the values would print account numbers to the terminal.
                raise ImportError_(
                    f"This file holds positions for {len(distinct)} accounts. Export a single "
                    f"account, or choose one with --only TEXT (matched within the "
                    f"{columns.account!r} column)."
                )

    holdings: list[Holding] = []
    skipped: list[str] = []
    for line_no, record in numbered:
        if not any(isinstance(v, str) and v.strip() for v in record.values()):
            continue
        if columns.currency:
            parse_currency_code(_cell(record, columns.currency))  # raises on non-USD (§12.1)

        raw_symbol = _cell(record, columns.symbol)
        symbol = normalize_symbol(raw_symbol)
        looks_like_symbol = bool(symbol) and bool(_SYMBOL_SHAPE.match(symbol))
        raw_value = _cell(record, columns.value)

        if not raw_value:
            if looks_like_symbol:
                skipped.append(f"line {line_no}: {symbol} has no market value")
            continue
        try:
            value = Money.parse(raw_value)
        except MoneyParseError as exc:
            if not looks_like_symbol:
                continue  # footer or disclaimer text spilling into the value column
            raise ImportError_(f"line {line_no}: {symbol}: {exc}") from exc

        if not looks_like_symbol:
            label = raw_symbol[:40] or "(blank symbol)"
            skipped.append(f"line {line_no}: {label!r} ({value.format()}) isn't a holding and was left out")
            continue

        quantity = None
        raw_quantity = _cell(record, columns.quantity)
        if raw_quantity:
            try:
                quantity = Decimal(raw_quantity.replace(",", ""))
            except InvalidOperation as exc:
                raise ImportError_(f"line {line_no}: {symbol}: unreadable quantity {raw_quantity!r}") from exc

        holdings.append(Holding(symbol, _cell(record, columns.name) or None, quantity, value))

    if not holdings:
        raise ImportError_("No holdings found in the file.")
    return holdings, skipped


def import_holdings(
    conn: sqlite3.Connection,
    path: Path,
    *,
    account_id: int,
    account_name: str,
    as_of: date,
    only: str | None = None,
    update_balance: bool = True,
) -> HoldingsResult:
    """Import one holdings export as the account's positions on `as_of`."""
    text = _read_text(path)
    # The --only filter is part of the identity: the same file filtered two ways is two imports.
    digest = hashlib.sha256(f"{text}\0{only or ''}".encode()).hexdigest()
    result = HoldingsResult(account=account_name, as_of=as_of)

    seen = conn.execute(
        "SELECT id FROM import_run WHERE account_id = ? AND file_sha256 = ? AND status = 'ok'",
        (account_id, digest),
    ).fetchone()
    if seen:
        result.skipped_file = True
        return result

    holdings, skipped = parse_holdings(text, only=only)
    result.holdings, result.skipped_rows = holdings, skipped

    run_id = int(
        conn.execute(
            """INSERT INTO import_run (account_id, source_name, file_sha256, row_count, status)
               VALUES (?, ?, ?, ?, 'ok')""",
            (account_id, path.name, digest, len(holdings)),
        ).lastrowid
    )
    result.positions_written = replace_positions(
        conn, account_id, as_of, holdings, source="import", import_run_id=run_id
    )
    conn.execute("UPDATE import_run SET inserted_count = ? WHERE id = ?", (result.positions_written, run_id))

    result.total = sum((h.market_value for h in holdings if h.market_value is not None), Money(0))
    if update_balance:
        set_balance(conn, account_id, result.total, as_of, source="import", import_run_id=run_id)
        result.balance_updated = True
    return result
