"""CSV / transaction-export import.

Columns are detected from the header rather than configured per institution.
That is a privacy decision as much as a convenience one: a repo full of
`chase.yml`, `amex.yml` mapping files discloses where you bank, which is the same
class of leak PRD §9.1 guards against for holdings. Detection needs no such file.

The two failure modes worth caring about, both silent:

* **Double-counting.** Re-downloading a statement over a wider date range must not
  re-add the days already imported. Handled by a per-transaction dedupe hash.
* **Sign inversion.** Credit-card exports commonly report purchases as positive.
  Getting this backwards turns debt into an asset. Never guessed silently — see
  `SignConvention` and the warning in `import_file`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from ..money import Money, MoneyParseError, parse_currency_code

__all__ = ["import_file", "ImportResult", "ImportError_", "SignConvention", "detect_columns"]


class ImportError_(Exception):
    """Raised when a file cannot be imported. Named to avoid shadowing builtins."""


class SignConvention(StrEnum):
    NATURAL = "natural"   # negative = money out (typical bank export)
    FLIPPED = "flipped"   # positive = money out (typical credit-card export)


# --- Header vocabulary ----------------------------------------------------
# Lowercased, punctuation-stripped header names seen across US institutions.
_DATE_HEADERS = {
    "date", "posted date", "post date", "posting date", "transaction date",
    "trans date", "date posted", "effective date", "settlement date",
}
_DESC_HEADERS = {
    "description", "payee", "name", "merchant", "memo", "details", "note",
    "transaction description", "original description", "description1",
}
_AMOUNT_HEADERS = {"amount", "transaction amount", "amt", "value"}
_DEBIT_HEADERS = {"debit", "withdrawal", "withdrawals", "money out", "paid out", "charges"}
_CREDIT_HEADERS = {"credit", "deposit", "deposits", "money in", "paid in", "payments"}
_CURRENCY_HEADERS = {"currency", "currency code", "ccy"}
_BALANCE_HEADERS = {"balance", "running balance", "ending balance", "current balance"}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def _norm_header(value: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (value or "").strip().lower())).strip()


@dataclass
class ColumnMap:
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    currency: str | None = None
    balance: str | None = None


def detect_columns(headers: list[str]) -> ColumnMap:
    """Map a header row onto known roles, or explain precisely what's missing."""
    found: dict[str, str] = {}
    for raw in headers:
        norm = _norm_header(raw)
        if norm in _DATE_HEADERS and "date" not in found:
            found["date"] = raw
        elif norm in _DESC_HEADERS and "description" not in found:
            found["description"] = raw
        elif norm in _AMOUNT_HEADERS and "amount" not in found:
            found["amount"] = raw
        elif norm in _DEBIT_HEADERS and "debit" not in found:
            found["debit"] = raw
        elif norm in _CREDIT_HEADERS and "credit" not in found:
            found["credit"] = raw
        elif norm in _CURRENCY_HEADERS and "currency" not in found:
            found["currency"] = raw
        elif norm in _BALANCE_HEADERS and "balance" not in found:
            found["balance"] = raw

    missing = []
    if "date" not in found:
        missing.append("a date column")
    if "description" not in found:
        missing.append("a description/payee column")
    if "amount" not in found and not ("debit" in found or "credit" in found):
        missing.append("an amount column (or debit/credit pair)")
    if missing:
        raise ImportError_(
            "Could not identify " + ", ".join(missing) + f".\n  Headers seen: {headers}\n"
            "  Add a mapping override in config/local/ (see config/import-profile.example.yml)."
        )
    return ColumnMap(**found)


# --- Dates ----------------------------------------------------------------
_DATE_FORMATS_ISO = ("%Y-%m-%d", "%Y/%m/%d")
_DATE_FORMATS_TEXT = ("%d %b %Y", "%b %d %Y", "%d-%b-%Y", "%b %d, %Y")


def _split_numeric_date(text: str) -> tuple[int, int, int] | None:
    parts = re.split(r"[/\-.]", text)
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    a, b, c = (int(p) for p in parts)
    if len(parts[0]) == 4:
        return None  # ISO, handled elsewhere
    year = c + 2000 if c < 100 else c
    return a, b, year


def infer_day_first(samples: list[str]) -> bool:
    """Decide MM/DD vs DD/MM for a whole file, not row by row.

    Deciding per row is how a file ends up with some dates read one way and some
    the other. If any first component exceeds 12 it cannot be a month, so the file
    is day-first; otherwise assume US month-first, which is right for USD-only.
    """
    for text in samples:
        parsed = _split_numeric_date(text.strip())
        if parsed and parsed[0] > 12:
            return True
    return False


def parse_date(text: str, *, day_first: bool = False) -> date:
    value = (text or "").strip()
    if not value:
        raise ImportError_("empty date")
    # Strip a time component if present.
    value = value.split("T")[0].split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value

    for fmt in _DATE_FORMATS_ISO:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    parsed = _split_numeric_date(value)
    if parsed:
        a, b, year = parsed
        day, month = (a, b) if day_first else (b, a)
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ImportError_(f"invalid date {text!r}") from exc

    for fmt in _DATE_FORMATS_TEXT:
        try:
            return datetime.strptime(value.replace(",", ""), fmt.replace(",", "")).date()
        except ValueError:
            pass
    raise ImportError_(f"unrecognized date format: {text!r}")


# --- Row model ------------------------------------------------------------
@dataclass
class ParsedRow:
    posted_on: date
    amount: Money
    description: str
    raw_description: str
    line_no: int


def _normalize_description(text: str) -> str:
    return _WS.sub(" ", (text or "").strip()).upper()


def dedupe_hash(posted_on: date, amount: Money, description: str, ordinal: int) -> str:
    """Stable identity for a transaction across separate exports.

    The ordinal is what makes this correct rather than merely plausible: two
    genuinely distinct $4.50 coffees on the same day at the same merchant would
    otherwise collide and the second would be dropped as a duplicate. Numbering
    repeats within a day means re-importing an overlapping range reproduces the
    same ordinals — so real duplicates still match, and real repeats survive.
    """
    key = f"{posted_on.isoformat()}|{amount.cents}|{_normalize_description(description)}|{ordinal}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@dataclass
class ImportResult:
    account: str
    rows_read: int = 0
    inserted: int = 0
    duplicates: int = 0
    skipped_file: bool = False
    warnings: list[str] = field(default_factory=list)
    date_range: tuple[date, date] | None = None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_(f"could not decode {path.name} as text")


def _find_header_row(lines: list[str]) -> int:
    """Skip preamble rows. Many exports lead with account/date banner lines."""
    for index, line in enumerate(lines[:15]):
        norm = {_norm_header(c) for c in next(csv.reader([line]), [])}
        if norm & _DATE_HEADERS and (norm & _DESC_HEADERS or norm & _AMOUNT_HEADERS):
            return index
    return 0


def parse_rows(
    text: str, *, sign: SignConvention = SignConvention.NATURAL
) -> tuple[list[ParsedRow], ColumnMap, list[str]]:
    lines = text.splitlines()
    if not lines:
        raise ImportError_("file is empty")

    start = _find_header_row(lines)
    body = "\n".join(lines[start:])
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(body), dialect=dialect)
    if not reader.fieldnames:
        raise ImportError_("no header row found")

    columns = detect_columns([f for f in reader.fieldnames if f is not None])
    records = list(reader)
    warnings: list[str] = []

    day_first = infer_day_first([r.get(columns.date, "") or "" for r in records])
    if day_first:
        warnings.append("Dates read as DD/MM (a day value above 12 appears in the file).")

    rows: list[ParsedRow] = []
    for offset, record in enumerate(records, start=start + 2):
        if not any((v or "").strip() for v in record.values()):
            continue  # blank line

        if columns.currency:
            parse_currency_code(record.get(columns.currency))  # raises on non-USD

        raw_desc = (record.get(columns.description) or "").strip()
        try:
            posted = parse_date(record.get(columns.date, ""), day_first=day_first)
            amount = _row_amount(record, columns)
        except (ImportError_, MoneyParseError) as exc:
            raise ImportError_(f"line {offset}: {exc}") from exc

        if sign is SignConvention.FLIPPED:
            amount = -amount

        rows.append(
            ParsedRow(
                posted_on=posted,
                amount=amount,
                description=_normalize_description(raw_desc),
                raw_description=raw_desc,
                line_no=offset,
            )
        )
    return rows, columns, warnings


def _row_amount(record: dict, columns: ColumnMap) -> Money:
    if columns.amount:
        return Money.parse(record.get(columns.amount, ""))

    # Split debit/credit columns: exactly one side is populated per row.
    debit = (record.get(columns.debit) or "").strip() if columns.debit else ""
    credit = (record.get(columns.credit) or "").strip() if columns.credit else ""
    if debit and credit:
        raise ImportError_("both debit and credit populated; cannot determine amount")
    if debit:
        return -abs(Money.parse(debit))
    if credit:
        return abs(Money.parse(credit))
    raise ImportError_("neither debit nor credit populated")


def import_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    account_id: int,
    account_name: str,
    is_liability: bool = False,
    sign: SignConvention = SignConvention.NATURAL,
) -> ImportResult:
    """Import one export file. Idempotent by file hash, deduplicated by row."""
    text = _read_text(path)
    file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    seen = conn.execute(
        "SELECT id FROM import_run WHERE account_id = ? AND file_sha256 = ? AND status = 'ok'",
        (account_id, file_hash),
    ).fetchone()
    if seen:
        return ImportResult(account=account_name, skipped_file=True)

    rows, _columns, warnings = parse_rows(text, sign=sign)
    result = ImportResult(account=account_name, rows_read=len(rows), warnings=warnings)
    if not rows:
        return result

    result.date_range = (min(r.posted_on for r in rows), max(r.posted_on for r in rows))

    # Sign sanity check. Not auto-corrected: guessing wrong silently converts debt
    # into an asset, so this surfaces the ambiguity and leaves the call to a human.
    positives = sum(1 for r in rows if r.amount.cents > 0)
    if is_liability and sign is SignConvention.NATURAL and positives > len(rows) * 0.7:
        warnings.append(
            f"{positives}/{len(rows)} amounts are positive on a liability account. "
            "Credit-card exports usually report purchases as positive — if these are "
            "charges rather than payments, re-run with --sign flipped."
        )

    cur = conn.execute(
        """INSERT INTO import_run (account_id, source_name, file_sha256, row_count, status)
           VALUES (?, ?, ?, ?, 'ok')""",
        (account_id, path.name, file_hash, len(rows)),
    )
    run_id = int(cur.lastrowid)

    ordinals: Counter[tuple[date, int, str]] = Counter()
    for row in rows:
        key = (row.posted_on, row.amount.cents, row.description)
        ordinals[key] += 1
        digest = dedupe_hash(row.posted_on, row.amount, row.description, ordinals[key])

        inserted = conn.execute(
            """INSERT INTO txn (account_id, posted_on, amount_cents, description,
                                raw_description, dedupe_hash, import_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, dedupe_hash) DO NOTHING""",
            (
                account_id, row.posted_on.isoformat(), row.amount.cents,
                row.description, row.raw_description, digest, run_id,
            ),
        )
        if inserted.rowcount:
            result.inserted += 1
        else:
            result.duplicates += 1

    conn.execute(
        "UPDATE import_run SET inserted_count = ?, duplicate_count = ? WHERE id = ?",
        (result.inserted, result.duplicates, run_id),
    )
    return result
