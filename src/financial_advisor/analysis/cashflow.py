"""Monthly spending and income derived from imported transactions (F3.1, F3.10).

Pure: transaction records in, summary out. No database, no clock.

The hard part is not the arithmetic, it's deciding what counts. Money moving between
your own accounts is neither spending nor income, and misclassifying it corrupts
both figures in a way that *looks like a finding*: a $2,000 monthly transfer to a
brokerage account counted as spending turns a 20% savings rate into zero.

Rows fall into three groups, handled differently on purpose:

* **Paired transfers** — an outflow from one of your accounts and an equal inflow to
  another within a few days, where at least one side is worded like a movement of
  money. Both sides are excluded. This is the reliable case, and it covers card
  payments whenever both the card and the paying account are imported.
* **Unpaired transfers** — "TRANSFER", "XFER" with no counterpart, usually because
  the other account isn't imported. Excluded, but totalled and reported so the
  exclusion is visible rather than silent.
* **Unpaired card payments** — a card payment leaving checking when the card itself
  isn't imported. *Counted* as spending: it is the only trace of that card's
  purchases. Excluding it would make spending on an untracked card vanish.

Peer-to-peer payments (Zelle, Venmo, …) are never treated as transfers, however
they're worded — rent paid by Zelle is spending.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..money import Money

__all__ = [
    "TxnRecord",
    "AccountCoverage",
    "CashFlowSummary",
    "SPENDING_ACCOUNT_TYPES",
    "classify",
    "match_transfers",
    "summarize_cashflow",
]

SPENDING_ACCOUNT_TYPES = frozenset({"checking", "savings", "money_market", "credit_card"})
DAYS_PER_MONTH = Decimal("30.4375")  # 365.25 / 12

_P2P = re.compile(r"\b(ZELLE|VENMO|CASH ?APP|PAYPAL|APPLE CASH)\b")
_TRANSFER = re.compile(r"\b(TRANSFER|XFER|TRNSFR|TFR)\b")
# Contributions to your own investment or retirement accounts that aren't imported,
# e.g. "VANGUARD BUY ACH" is not caught, but "BROKERAGE CONTRIBUTION" and "ROTH IRA"
# are. Without this, a monthly brokerage deposit counts as spending and the savings
# rate reads near zero — a wrong result that looks like a finding.
_INVESTING = re.compile(
    r"\b(BROKERAGE|INVESTMENTS?|INVEST|CONTRIBUTION|IRA|ROTH|401 ?K|403 ?B|529 PLAN|HSA)\b"
)
_CARD_PAYMENT = re.compile(
    r"PAYMENT THANK YOU|AUTOPAY|AUTO PAY|AUTOMATIC PAYMENT|CARD PAYMENT|CRD PMT|"
    r"CREDIT CARD|ONLINE PAYMENT|EPAY|PAYMENT RECEIVED"
)
# Wording that makes an equal-and-opposite pair plausibly a movement between your own
# accounts rather than coincidence. Without requiring one side to match, a $12.99
# charge and an unrelated $12.99 inflow two days apart would cancel each other out.
_MOVEMENT = re.compile(
    r"\b(TRANSFER|XFER|TRNSFR|TFR|PAYMENT|PMT|ACH|DEPOSIT|CONTRIBUTION|WITHDRAWAL|"
    r"FUNDS|ONLINE BANKING|MOBILE BANKING|AUTOPAY|BROKERAGE|INVESTMENTS?)\b"
)


@dataclass(frozen=True)
class TxnRecord:
    id: int
    account_id: int
    account_type: str
    posted_on: date
    amount: Money
    description: str


@dataclass(frozen=True)
class AccountCoverage:
    account_id: int
    first: date
    last: date
    days: int
    # How far this account's newest transaction trails the newest data overall. A
    # large lag usually means an export that wasn't refreshed, which dilutes that
    # account's monthly figure.
    lag_days: int


@dataclass(frozen=True)
class CashFlowSummary:
    monthly_spending: Money
    monthly_income: Money
    first_date: date
    last_date: date
    shortest_coverage_days: int
    coverage: tuple[AccountCoverage, ...]
    paired_transfer_count: int
    paired_transfer_total: Money
    unpaired_transfers_out: Money
    unpaired_transfers_in: Money
    unpaired_payments_counted: Money

    def is_reliable(self, min_days: int) -> bool:
        return self.shortest_coverage_days >= min_days


def classify(description: str, account_type: str, amount: Money) -> str:
    """How an unpaired row is treated: 'transfer', 'card_payment', or 'ordinary'."""
    text = description.upper()
    if _P2P.search(text):
        return "ordinary"
    if account_type == "credit_card" and amount.cents > 0 and _CARD_PAYMENT.search(text):
        return "transfer"  # a payment received on the card, not a refund
    if _TRANSFER.search(text):
        return "transfer"
    if amount.cents < 0 and _INVESTING.search(text):
        return "transfer"  # saving into an account that isn't imported, not spending
    if account_type != "credit_card" and amount.cents < 0 and _CARD_PAYMENT.search(text):
        return "card_payment"
    return "ordinary"


def _plausible_movement(a: TxnRecord, b: TxnRecord) -> bool:
    texts = (a.description.upper(), b.description.upper())
    if any(_P2P.search(t) for t in texts):
        return False
    return any(_MOVEMENT.search(t) or _CARD_PAYMENT.search(t) for t in texts)


def match_transfers(txns: list[TxnRecord], window_days: int) -> set[int]:
    """Ids of transactions that are one side of a transfer between your own accounts.

    Greedy in date order, choosing the closest-dated counterpart. Each transaction
    pairs at most once, so two identical transfers in one week pair off one-to-one
    instead of both claiming the same inflow.
    """
    inflows: dict[int, list[TxnRecord]] = defaultdict(list)
    for txn in txns:
        if txn.amount.cents > 0:
            inflows[txn.amount.cents].append(txn)

    matched: set[int] = set()
    outflows = sorted((t for t in txns if t.amount.cents < 0), key=lambda t: (t.posted_on, t.id))
    for out in outflows:
        best: TxnRecord | None = None
        best_key: tuple[int, date, int] | None = None
        for candidate in inflows.get(-out.amount.cents, ()):
            if candidate.id in matched or candidate.account_id == out.account_id:
                continue
            gap = abs((candidate.posted_on - out.posted_on).days)
            if gap > window_days or not _plausible_movement(out, candidate):
                continue
            key = (gap, candidate.posted_on, candidate.id)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        if best is not None:
            matched.update((out.id, best.id))
    return matched


def _monthly(total: Money, days: int) -> Money:
    """A total over `days` as a monthly rate, rounded exactly once.

    Multiplying by a precomputed DAYS_PER_MONTH / days factor would round that factor
    to 28 digits first, which can tip an exact half-cent the wrong way.
    """
    exact = Decimal(total.cents) * DAYS_PER_MONTH / Decimal(days)
    return Money.from_cents(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def summarize_cashflow(
    txns: list[TxnRecord],
    *,
    as_of: date,
    window_days: int,
    max_history_days: int,
) -> CashFlowSummary | None:
    """Monthly spending and income, or None when no spending-account data exists.

    Scaled to a monthly rate per account, then summed. Averaging over one shared
    window instead would understate an account with a shorter history — a card
    imported for one month would have its spending spread across six.
    """
    start = as_of - timedelta(days=max_history_days)
    in_range = [t for t in txns if start <= t.posted_on <= as_of]
    spending_rows = [t for t in in_range if t.account_type in SPENDING_ACCOUNT_TYPES]
    if not spending_rows:
        return None

    # Every imported account participates in pairing — a transfer to a brokerage
    # account pairs with the brokerage deposit — but only spending accounts
    # contribute to the totals.
    paired = match_transfers(in_range, window_days)
    newest = max(t.posted_on for t in spending_rows)

    by_account: dict[int, list[TxnRecord]] = defaultdict(list)
    for txn in spending_rows:
        by_account[txn.account_id].append(txn)

    monthly_spending = Money(0)
    monthly_income = Money(0)
    unpaired_out = Money(0)
    unpaired_in = Money(0)
    payments_counted = Money(0)
    coverage: list[AccountCoverage] = []

    for account_id in sorted(by_account):
        rows = by_account[account_id]
        first, last = min(t.posted_on for t in rows), max(t.posted_on for t in rows)
        # Measured to the newest data overall, not this account's last row: a savings
        # account with one interest credit in six months has six months of coverage,
        # not one day — and one day would multiply that credit by thirty.
        days = (newest - first).days + 1
        coverage.append(AccountCoverage(account_id, first, last, days, (newest - last).days))

        spent, received = Money(0), Money(0)
        for txn in rows:
            if txn.id in paired:
                continue
            kind = classify(txn.description, txn.account_type, txn.amount)
            if kind == "transfer":
                if txn.amount.is_negative():
                    unpaired_out += abs(txn.amount)
                else:
                    unpaired_in += txn.amount
                continue
            if kind == "card_payment":
                payments_counted += abs(txn.amount)
            if txn.amount.is_negative():
                spent += abs(txn.amount)
            elif txn.account_type == "credit_card":
                spent -= txn.amount  # refund or statement credit
            else:
                received += txn.amount

        monthly_spending += _monthly(spent, days)
        monthly_income += _monthly(received, days)

    paired_outflows = [t for t in in_range if t.id in paired and t.amount.is_negative()]
    return CashFlowSummary(
        monthly_spending=monthly_spending,
        monthly_income=monthly_income,
        first_date=min(c.first for c in coverage),
        last_date=newest,
        shortest_coverage_days=min(c.days for c in coverage),
        coverage=tuple(coverage),
        paired_transfer_count=len(paired_outflows),
        paired_transfer_total=sum((abs(t.amount) for t in paired_outflows), Money(0)),
        unpaired_transfers_out=unpaired_out,
        unpaired_transfers_in=unpaired_in,
        unpaired_payments_counted=payments_counted,
    )
