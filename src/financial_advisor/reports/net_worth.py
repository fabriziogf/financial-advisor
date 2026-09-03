"""Net worth statement (F1.5).

Two things this does that a naive version would not:

* **Liabilities are subtracted via the account taxonomy**, not via the sign that
  happened to be in the export. Institutions are inconsistent — some report a card
  balance as -1,200 and some as 1,200 — so trusting the stored sign means a credit
  card can land on the asset side. `is_liability` comes from the schema.
* **Stale balances are flagged, not quietly counted as current.** R5: the tool has
  to be able to say it doesn't know. A total presented with full confidence on a
  six-month-old figure is worse than one with a visible gap, because it stops the
  reader from asking.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..db.store import Account, latest_balances, list_accounts
from ..money import Money

__all__ = ["NetWorth", "AccountBalance", "compute_net_worth", "STALE_AFTER_DAYS"]

# Beyond this, a balance is reported as stale. Roughly a monthly statement cycle
# plus slack — long enough not to nag, short enough that a forgotten account
# surfaces before it distorts a decision.
STALE_AFTER_DAYS = 45


@dataclass(frozen=True)
class AccountBalance:
    account: Account
    amount: Money           # as stored, signed
    as_of: date | None
    days_old: int | None

    @property
    def is_stale(self) -> bool:
        return self.days_old is not None and self.days_old > STALE_AFTER_DAYS

    @property
    def has_no_data(self) -> bool:
        return self.as_of is None

    @property
    def magnitude(self) -> Money:
        """Absolute size, for display. The asset/liability split carries the sign."""
        return abs(self.amount)


@dataclass
class NetWorth:
    as_of: date
    assets: list[AccountBalance] = field(default_factory=list)
    liabilities: list[AccountBalance] = field(default_factory=list)
    missing: list[AccountBalance] = field(default_factory=list)

    @property
    def total_assets(self) -> Money:
        return sum((b.magnitude for b in self.assets), Money(0))

    @property
    def total_liabilities(self) -> Money:
        return sum((b.magnitude for b in self.liabilities), Money(0))

    @property
    def net_worth(self) -> Money:
        return self.total_assets - self.total_liabilities

    @property
    def stale(self) -> list[AccountBalance]:
        return [b for b in self.assets + self.liabilities if b.is_stale]

    @property
    def is_complete(self) -> bool:
        """False when any account lacks data or is stale — the figure is partial."""
        return not self.missing and not self.stale

    def by_type(self, liability: bool) -> dict[str, list[AccountBalance]]:
        grouped: dict[str, list[AccountBalance]] = {}
        for bal in (self.liabilities if liability else self.assets):
            grouped.setdefault(bal.account.type_label, []).append(bal)
        return grouped


def compute_net_worth(conn: sqlite3.Connection, as_of: date | None = None) -> NetWorth:
    when = as_of or date.today()
    balances = latest_balances(conn, when)
    report = NetWorth(as_of=when)

    for account in list_accounts(conn, active_only=True):
        row = balances.get(account.id)
        if row is None:
            report.missing.append(
                AccountBalance(account=account, amount=Money(0), as_of=None, days_old=None)
            )
            continue

        recorded = date.fromisoformat(row["as_of_date"])
        entry = AccountBalance(
            account=account,
            amount=Money.from_cents(int(row["amount_cents"])),
            as_of=recorded,
            days_old=(when - recorded).days,
        )
        (report.liabilities if account.is_liability else report.assets).append(entry)

    return report


def format_net_worth(report: NetWorth) -> str:
    """Plain-text statement for the CLI."""
    lines: list[str] = []
    width = 56

    lines.append(f"Net worth as of {report.as_of.isoformat()}")
    lines.append("=" * width)

    for liability in (False, True):
        groups = report.by_type(liability)
        if not groups:
            continue
        heading = "LIABILITIES" if liability else "ASSETS"
        lines.append("")
        lines.append(heading)
        lines.append("-" * width)
        for label, entries in groups.items():
            lines.append(f"  {label}")
            for entry in entries:
                marker = "  ⚠" if entry.is_stale else ""
                name = entry.account.name
                if entry.account.institution:
                    name = f"{entry.account.institution} · {name}"
                # Truncate rather than let a long name push the amounts out of
                # alignment — a column of numbers that doesn't line up is harder
                # to scan for the outlier, which is the whole point of the report.
                if len(name) > 34:
                    name = name[:33] + "…"
                lines.append(f"    {name:<34} {entry.magnitude.format():>14}{marker}")
        subtotal = report.total_liabilities if liability else report.total_assets
        lines.append(f"  {'Subtotal':<36} {subtotal.format():>14}")

    lines.append("")
    lines.append("=" * width)
    lines.append(f"  {'NET WORTH':<36} {report.net_worth.format():>14}")
    lines.append("=" * width)

    # Caveats last, where they read as part of the answer rather than as decoration.
    if report.stale:
        lines.append("")
        lines.append(f"⚠ {len(report.stale)} balance(s) older than {STALE_AFTER_DAYS} days:")
        for entry in report.stale:
            lines.append(
                f"    {entry.account.name:<34} last updated {entry.as_of} "
                f"({entry.days_old} days ago)"
            )
        lines.append("  This total is only as current as its oldest input.")

    if report.missing:
        lines.append("")
        lines.append(f"⚠ {len(report.missing)} account(s) have no balance and are EXCLUDED:")
        for entry in report.missing:
            lines.append(f"    {entry.account.name}")
        lines.append("  The figure above is incomplete until these are populated.")

    return "\n".join(lines)


def stale_cutoff(as_of: date) -> date:
    return as_of - timedelta(days=STALE_AFTER_DAYS)
