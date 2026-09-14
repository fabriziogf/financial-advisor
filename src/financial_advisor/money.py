"""Money — the foundational type.

Two invariants, both from the PRD, both enforced here rather than assumed:

* **R2: money is never a float.** `Money` refuses construction from `float`
  outright. Accepting one would be the single most damaging silent failure in the
  system — 0.1 + 0.2 quietly becoming 0.30000000000000004 in a net worth total is
  wrong in a way nothing visibly breaks.
* **§12.1: USD only** (D8). A currency code that is not USD raises at ingest. The
  failure this prevents is a foreign amount summed as dollars, which corrupts the
  total while every row still looks plausible.

Internally an integer count of cents. That keeps arithmetic exact, matches the
storage encoding (SQLite `SUM()` on TEXT coerces to float, which would smuggle a
float back into the money path), and makes equality and hashing trivially correct.
`Decimal` is what callers see, per Appendix A.5.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Union

__all__ = [
    "Money",
    "MoneyError",
    "MoneyParseError",
    "NonUSDCurrencyError",
    "CURRENCY",
]

CURRENCY = "USD"

_CENTS = Decimal("0.01")


class MoneyError(Exception):
    """Base for money errors."""


class MoneyParseError(MoneyError, ValueError):
    """A string could not be read as a monetary amount."""


class NonUSDCurrencyError(MoneyError):
    """A non-USD currency was encountered. See PRD §12.1.

    Deliberately fatal rather than coerced: the system performs no FX conversion
    anywhere, so the only safe response to a foreign amount is to refuse it loudly.
    """


# Accepted: "$1,234.56"  "(1,234.56)"  "-1234.56"  "1 234,56" is NOT accepted
# (that is a European format and would be ambiguous against USD thousands commas).
_CLEAN_RE = re.compile(r"[,\s ]")
_CURRENCY_PREFIX_RE = re.compile(r"^(usd|us\$|\$)", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^[+-]?\d*\.?\d+$")

Numeric = Union[int, str, Decimal, "Money"]


class Money:
    """An exact USD amount, stored as integer cents."""

    __slots__ = ("_cents",)

    def __init__(self, value: Numeric = 0) -> None:
        self._cents = _to_cents(value)

    # --- constructors -----------------------------------------------------
    @classmethod
    def from_cents(cls, cents: int) -> Money:
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise TypeError(f"from_cents expects int, got {type(cents).__name__}")
        obj = cls.__new__(cls)
        obj._cents = cents
        return obj

    @classmethod
    def parse(cls, raw: str) -> Money:
        """Parse an amount as it appears in a real bank or brokerage export.

        Handles the formats these files actually use: currency symbols, thousands
        separators, accounting-style parentheses for negatives, and the unicode
        minus some institutions emit. An empty or unparseable value raises rather
        than defaulting to zero — a silent zero in an import is indistinguishable
        from a genuine zero balance, and one of those is a bug.
        """
        if raw is None:
            raise MoneyParseError("expected an amount, got None")
        if isinstance(raw, Money):
            return raw

        text = str(raw).strip()
        if not text:
            raise MoneyParseError("expected an amount, got an empty value")

        text = text.replace("−", "-")  # unicode minus
        negative = False

        # Accounting notation: (1,234.56) means -1234.56
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1].strip()

        # A sign ahead of the currency symbol ("-$1,234.56") is the most common way US
        # exports write a negative amount. Exactly one sign is accepted: "--5" and
        # "(-100)" are garbage, and reading them as numbers would hide a broken file.
        leading_sign = text[:1] if text[:1] in ("-", "+") else ""
        if leading_sign:
            if negative:
                raise MoneyParseError(f"could not parse {raw!r} as a USD amount")
            negative = leading_sign == "-"
            text = text[1:].strip()
        text = _CURRENCY_PREFIX_RE.sub("", text).strip()
        if leading_sign and text[:1] in ("-", "+"):
            raise MoneyParseError(f"could not parse {raw!r} as a USD amount")
        # A trailing sign ("1234.56-") shows up in some fixed-width exports.
        if text.endswith("-"):
            negative = True
            text = text[:-1].strip()
        text = _CLEAN_RE.sub("", text)

        if not text or not _NUMERIC_RE.match(text):
            raise MoneyParseError(f"could not parse {raw!r} as a USD amount")

        try:
            amount = Decimal(text)
        except InvalidOperation as exc:  # pragma: no cover - regex guards this
            raise MoneyParseError(f"could not parse {raw!r} as a USD amount") from exc

        if negative:
            amount = -amount
        return cls(amount)

    # --- accessors --------------------------------------------------------
    @property
    def cents(self) -> int:
        return self._cents

    @property
    def amount(self) -> Decimal:
        return (Decimal(self._cents) / 100).quantize(_CENTS)

    # --- arithmetic -------------------------------------------------------
    def __add__(self, other: Money) -> Money:
        return Money.from_cents(self._cents + _other_cents(other, "+"))

    __radd__ = __add__

    def __sub__(self, other: Money) -> Money:
        return Money.from_cents(self._cents - _other_cents(other, "-"))

    def __neg__(self) -> Money:
        return Money.from_cents(-self._cents)

    def __abs__(self) -> Money:
        return Money.from_cents(abs(self._cents))

    def __mul__(self, factor: int | Decimal) -> Money:
        """Scale by a quantity or rate — e.g. share count times price."""
        if isinstance(factor, float):
            raise TypeError(_FLOAT_MSG.format(op="multiply"))
        if isinstance(factor, bool) or not isinstance(factor, (int, Decimal)):
            raise TypeError(f"cannot multiply Money by {type(factor).__name__}")
        exact = Decimal(self._cents) * Decimal(factor)
        return Money.from_cents(int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))

    __rmul__ = __mul__

    def ratio_to(self, other: Money) -> Decimal:
        """This amount as a proportion of another. Returns Decimal, not Money."""
        if not isinstance(other, Money):
            raise TypeError("ratio_to expects Money")
        if other._cents == 0:
            raise ZeroDivisionError("cannot take a ratio against zero")
        return Decimal(self._cents) / Decimal(other._cents)

    # --- comparison -------------------------------------------------------
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self._cents == other._cents

    def __hash__(self) -> int:
        return hash(("Money", self._cents))

    def __lt__(self, other: Money) -> bool:
        return self._cents < _other_cents(other, "<")

    def __le__(self, other: Money) -> bool:
        return self._cents <= _other_cents(other, "<=")

    def __gt__(self, other: Money) -> bool:
        return self._cents > _other_cents(other, ">")

    def __ge__(self, other: Money) -> bool:
        return self._cents >= _other_cents(other, ">=")

    def __bool__(self) -> bool:
        return self._cents != 0

    def is_negative(self) -> bool:
        return self._cents < 0

    # --- display ----------------------------------------------------------
    def __repr__(self) -> str:
        return f"Money('{self.amount}')"

    def __str__(self) -> str:
        return self.format()

    def format(self, *, sign: bool = False) -> str:
        """Human-readable, with thousands separators. '-$1,234.56'."""
        whole = abs(self._cents) // 100
        frac = abs(self._cents) % 100
        body = f"${whole:,}.{frac:02d}"
        if self._cents < 0:
            return f"-{body}"
        return f"+{body}" if sign else body


_FLOAT_MSG = (
    "refusing to {op} Money with a float (PRD R2). Floats cannot represent most "
    "decimal amounts exactly, and the resulting error is silent. Use a str, int, "
    "or Decimal — e.g. Money('12.34'), not Money(12.34)."
)


def _to_cents(value: Numeric) -> int:
    if isinstance(value, Money):
        return value._cents
    if isinstance(value, float):
        raise TypeError(_FLOAT_MSG.format(op="construct"))
    if isinstance(value, bool):
        raise TypeError("refusing to construct Money from bool")
    if isinstance(value, int):
        return value * 100
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyParseError(f"could not read {value!r} as a decimal") from exc
    else:
        raise TypeError(f"cannot build Money from {type(value).__name__}")

    if not dec.is_finite():
        raise MoneyParseError(f"refusing a non-finite amount: {value!r}")
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _other_cents(other: object, op: str) -> int:
    if isinstance(other, Money):
        return other._cents
    # `sum()` seeds with int 0; allow that one case so sum(list_of_money) works.
    if isinstance(other, int) and not isinstance(other, bool) and other == 0:
        return 0
    raise TypeError(f"cannot use {op} between Money and {type(other).__name__}")


def parse_currency_code(code: str | None) -> str:
    """Validate a currency column from an import. Anything but USD raises.

    See PRD §12.1. A missing code is treated as USD — most US exports omit the
    column entirely, and inventing a rejection for that would block ordinary files.
    An explicitly stated foreign currency is a different matter and is refused.
    """
    if code is None:
        return CURRENCY
    text = str(code).strip().upper()
    if not text:
        return CURRENCY
    if text != CURRENCY:
        raise NonUSDCurrencyError(
            f"found currency {text!r}, but this system is USD-only (PRD D8/§12.1). "
            "No FX conversion is performed anywhere, so the amount cannot be "
            "safely imported. Track it as a manually-valued declared asset instead."
        )
    return CURRENCY
