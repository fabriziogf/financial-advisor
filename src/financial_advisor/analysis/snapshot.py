"""The snapshot: everything a check may look at, gathered once.

`build_snapshot` is the observation engine's only I/O — database, profile, rules,
cached benchmark rate. Everything downstream is a pure function of the frozen object
it returns, which is what lets each check be tested against a hand-built snapshot
with no database, no files, and no clock (R1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ..db.store import (
    Account,
    PositionRow,
    Terms,
    get_terms,
    latest_balances,
    latest_positions,
    list_accounts,
    transactions_between,
)
from ..money import Money
from ..profile import Profile, load_profile
from ..rates import BenchmarkRate, load_benchmark
from ..reports.net_worth import STALE_AFTER_DAYS
from ..rules import Rules, Thresholds, load_rules
from .cashflow import CashFlowSummary, TxnRecord, summarize_cashflow
from .model import fmt_number

__all__ = ["AccountState", "ExpenseBasis", "Snapshot", "build_snapshot", "LIQUID_TYPES"]

LIQUID_TYPES = frozenset({"checking", "savings", "money_market"})


@dataclass(frozen=True)
class AccountState:
    account: Account
    balance: Money | None             # signed, as stored
    balance_as_of: date | None
    terms: Terms | None = None
    positions: tuple[PositionRow, ...] = ()

    @property
    def name(self) -> str:
        return self.account.name

    @property
    def positions_as_of(self) -> date | None:
        return max(p.as_of for p in self.positions) if self.positions else None

    def is_stale(self, as_of: date) -> bool:
        return self.balance_as_of is not None and (as_of - self.balance_as_of).days > STALE_AFTER_DAYS


@dataclass(frozen=True)
class ExpenseBasis:
    amount: Money
    declared: bool   # True when stated in the profile rather than derived
    label: str


@dataclass(frozen=True)
class Snapshot:
    as_of: date
    accounts: tuple[AccountState, ...]
    rules: Rules
    profile: Profile | None = None
    benchmark: BenchmarkRate | None = None
    cashflow: CashFlowSummary | None = None

    @property
    def thresholds(self) -> Thresholds:
        return self.rules.thresholds

    def of_types(self, types: frozenset[str] | set[str]) -> list[AccountState]:
        return [s for s in self.accounts if s.account.type_code in types]

    def liquid(self) -> list[AccountState]:
        return self.of_types(LIQUID_TYPES)

    def investments(self) -> list[AccountState]:
        return [s for s in self.accounts if s.account.is_investment]

    def liabilities(self) -> list[AccountState]:
        return [s for s in self.accounts if s.account.is_liability]

    def account_name(self, account_id: int) -> str:
        for state in self.accounts:
            if state.account.id == account_id:
                return state.name
        return f"account {account_id}"

    def liquid_total(self) -> Money | None:
        """Sum of liquid balances, or None if any is unknown.

        None rather than a partial sum: a savings account with no recorded balance
        may well be the emergency fund, and a total that silently omits it would
        report a shortfall that isn't real.
        """
        liquid = self.liquid()
        if not liquid or any(s.balance is None for s in liquid):
            return None
        return sum((s.balance for s in liquid if s.balance is not None), Money(0))

    def net_worth(self) -> Money:
        """Recorded balances only; the taxonomy, not the stored sign, decides the side."""
        total = Money(0)
        for state in self.accounts:
            if state.balance is None:
                continue
            if state.account.is_liability:
                total -= abs(state.balance)
            else:
                total += abs(state.balance)
        return total

    def expense_basis(self) -> ExpenseBasis | None:
        if self.profile and self.profile.monthly_essential_expenses is not None:
            return ExpenseBasis(
                self.profile.monthly_essential_expenses, True, "declared essential expenses"
            )
        min_days = self.thresholds.integer("cashflow", "min_days_of_history")
        flow = self.cashflow
        if flow and flow.is_reliable(min_days) and flow.monthly_spending.cents > 0:
            return ExpenseBasis(
                flow.monthly_spending,
                False,
                f"average total spending, {flow.first_date} to {flow.last_date}",
            )
        return None

    def emergency_target(self) -> tuple[Decimal, str | None]:
        """Target months, plus an assumption note when a default was used."""
        if self.profile and self.profile.emergency_target_months is not None:
            return self.profile.emergency_target_months, None
        if self.profile and self.profile.income_stability == "variable":
            months = self.thresholds.decimal("emergency_fund", "variable_income_target_months")
            reason = "the default for variable income"
        else:
            months = self.thresholds.decimal("emergency_fund", "default_target_months")
            reason = "the default"
        return months, (
            f"The {fmt_number(months)}-month target is {reason} in rules/thresholds.yml; "
            "set emergency_fund.target_months in your profile to use your own."
        )


def build_snapshot(
    conn: sqlite3.Connection,
    *,
    as_of: date,
    rules: Rules | None = None,
    profile_file: Path | None = None,
    benchmark_file: Path | None = None,
) -> Snapshot:
    """Gather every input. Raises ProfileError on a malformed profile.

    A malformed profile stops the run rather than being skipped: what you meant is
    unknown, and every check that reads the profile would otherwise report
    "insufficient data" for information you believe you've already provided.
    """
    rules = rules or load_rules(as_of.year)
    profile = load_profile(profile_file, asset_classes=set(rules.asset_classes), today=as_of)
    benchmark = load_benchmark(benchmark_file)

    balances = latest_balances(conn, as_of)
    terms = get_terms(conn)
    positions = latest_positions(conn, as_of)
    states: list[AccountState] = []
    for account in list_accounts(conn, active_only=True):
        row = balances.get(account.id)
        states.append(
            AccountState(
                account=account,
                balance=Money.from_cents(int(row["amount_cents"])) if row else None,
                balance_as_of=date.fromisoformat(row["as_of_date"]) if row else None,
                terms=terms.get(account.id),
                positions=tuple(positions.get(account.id, ())),
            )
        )

    thresholds = rules.thresholds
    max_days = thresholds.integer("cashflow", "max_history_days")
    txns = [
        TxnRecord(
            id=int(r["id"]),
            account_id=int(r["account_id"]),
            account_type=r["type_code"],
            posted_on=date.fromisoformat(r["posted_on"]),
            amount=Money.from_cents(int(r["amount_cents"])),
            description=r["description"],
        )
        for r in transactions_between(conn, as_of - timedelta(days=max_days), as_of)
    ]
    cashflow = summarize_cashflow(
        txns,
        as_of=as_of,
        window_days=thresholds.integer("cashflow", "transfer_match_window_days"),
        max_history_days=max_days,
    )
    return Snapshot(
        as_of=as_of,
        accounts=tuple(states),
        rules=rules,
        profile=profile,
        benchmark=benchmark,
        cashflow=cashflow,
    )
