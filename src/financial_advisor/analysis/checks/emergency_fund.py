"""F3.1 — Emergency fund: months of expenses covered by liquid cash."""

from __future__ import annotations

from ..model import Fact, Observation, Severity, fmt_months, fmt_number
from ..snapshot import Snapshot
from ._common import attention, insufficient, names, ok, stale_note

TITLE = "Emergency fund"
KEY = "F3.1.coverage"


def check(snapshot: Snapshot) -> list[Observation]:
    min_days = snapshot.thresholds.integer("cashflow", "min_days_of_history")
    liquid = snapshot.liquid()
    if not liquid:
        return [
            insufficient(
                KEY,
                TITLE,
                "No checking, savings, or money market accounts are recorded.",
                [
                    "Add cash accounts with `fa account-add --type savings` (or checking, "
                    "money_market) and record balances with `fa balance`."
                ],
            )
        ]

    missing: list[str] = []
    unbalanced = [s for s in liquid if s.balance is None]
    if unbalanced:
        missing.append(f"Balances for {names(unbalanced)}: `fa balance --account NAME --amount AMOUNT`.")
    basis = snapshot.expense_basis()
    if basis is None:
        missing.append(
            f"Monthly expenses: import at least {min_days} days of checking and credit card "
            "transactions with `fa import`, or set emergency_fund.monthly_essential_expenses "
            "in your profile."
        )
    liquid_total = snapshot.liquid_total()
    if missing or basis is None or liquid_total is None:
        return [insufficient(KEY, TITLE, "Emergency-fund depth can't be measured yet.", missing)]

    months = liquid_total.ratio_to(basis.amount)
    target, target_note = snapshot.emergency_target()
    target_amount = basis.amount * target

    facts = [
        Fact("Liquid cash", liquid_total.format()),
        Fact("Monthly expenses", f"{basis.amount.format()} ({basis.label})"),
        Fact("Months covered", fmt_months(months)),
        Fact("Target", f"{fmt_number(target)} months ({target_amount.format()})"),
    ]
    assumptions = [
        "Liquid cash is checking, savings, and money market balances. CDs and investment "
        "accounts are excluded: they can't be reached without penalty or market risk."
    ]
    if not basis.declared:
        assumptions.append(
            "Expenses are total spending from imported transactions, including discretionary "
            "costs, so this understates how long essentials alone would last. Set "
            "emergency_fund.monthly_essential_expenses to use your own figure."
        )
    if target_note:
        assumptions.append(target_note)
    note = stale_note(liquid, snapshot.as_of)
    if note:
        assumptions.append(note)
    inputs = (f"Balances: {names(liquid)}", f"Expenses: {basis.label}")

    covered = f"Liquid cash of {liquid_total.format()} covers {fmt_months(months)} months of expenses"
    if months >= target:
        facts.append(Fact("Above target", (liquid_total - target_amount).format()))
        return [
            ok(
                KEY,
                TITLE,
                f"{covered}, meeting the {fmt_number(target)}-month target.",
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]

    shortfall = target_amount - liquid_total
    facts.append(Fact("Shortfall", shortfall.format()))
    if months < 1:
        severity = Severity.HIGH
    elif months < target / 2:
        severity = Severity.MEDIUM
    else:
        severity = Severity.LOW
    return [
        attention(
            KEY,
            TITLE,
            severity,
            f"{covered}, {shortfall.format()} short of the {fmt_number(target)}-month target.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
        )
    ]
