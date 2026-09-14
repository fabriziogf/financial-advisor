"""F3.8 — Debt: interest cost, payoff ordering, and rate risks.

Paying down a debt earns its interest rate with certainty. That's the comparison
made here — against the risk-free Treasury bill rate — rather than against an
assumed market return, which would bake a forecast into the finding.
"""

from __future__ import annotations

from ...money import Money
from ..model import Fact, Observation, Severity, fmt_rate
from ..snapshot import AccountState, Snapshot
from ._common import attention, insufficient, names, not_applicable, ok, stale_note

TITLE = "Debt"
KEY = "F3.8.debt"


def _balance(state: AccountState) -> Money:
    assert state.balance is not None
    return abs(state.balance)


def _rate(state: AccountState):  # -> Decimal
    assert state.terms is not None and state.terms.rate is not None
    return state.terms.rate


def _interest(state: AccountState) -> Money:
    return _balance(state) * _rate(state)


def check(snapshot: Snapshot) -> list[Observation]:
    thresholds = snapshot.thresholds
    liabilities = snapshot.liabilities()
    if not liabilities:
        return [not_applicable(KEY, TITLE, "No debts are recorded.")]

    missing: list[str] = []
    unbalanced = [s for s in liabilities if s.balance is None]
    if unbalanced:
        missing.append(f"Balances for {names(unbalanced)}.")

    rated: list[AccountState] = []
    paid_in_full: list[AccountState] = []
    for state in liabilities:
        if state.balance is None or state.balance.cents == 0:
            continue
        terms = state.terms
        if state.account.type_code == "credit_card":
            if terms is None or terms.revolving is None:
                missing.append(
                    f"Whether {state.name} carries a balance from month to month: "
                    f'`fa terms --account "{state.name}" --revolving` or `--pays-in-full`.'
                )
                continue
            if not terms.revolving:
                paid_in_full.append(state)
                continue
        if terms is None or terms.rate is None:
            missing.append(
                f'The interest rate for {state.name}: `fa terms --account "{state.name}" '
                "--rate PERCENT --kind fixed` (or variable)."
            )
            continue
        rated.append(state)

    assumptions = [
        "Interest is estimated as balance × rate for a year; actual interest depends on "
        "compounding and on payments made.",
    ]
    if paid_in_full:
        assumptions.append(f"Cards paid in full each month accrue no interest and are excluded: {names(paid_in_full)}.")

    if not rated:
        if missing:
            return [insufficient(KEY, TITLE, "No debt has enough terms recorded to analyze.", missing, assumptions=assumptions)]
        return [
            ok(
                KEY,
                TITLE,
                "No interest-bearing debt: every card balance recorded is paid in full each month.",
                assumptions=assumptions,
            )
        ]

    total = sum((_balance(s) for s in rated), Money(0))
    interest = sum((_interest(s) for s in rated), Money(0))
    weighted = interest.ratio_to(total)
    high_rate = thresholds.decimal("debt", "high_rate")
    high = [s for s in rated if _rate(s) >= high_rate]
    high_interest = sum((_interest(s) for s in high), Money(0))

    avalanche = sorted(rated, key=lambda s: (-_rate(s), _balance(s).cents, s.name))
    snowball = sorted(rated, key=lambda s: (_balance(s).cents, -_rate(s), s.name))
    detail = ["Highest rate first:"]
    for position, state in enumerate(avalanche, start=1):
        kind = f" {state.terms.rate_kind}" if state.terms and state.terms.rate_kind else ""
        detail.append(
            f"{position}. {state.name}: {_balance(state).format()} at {fmt_rate(_rate(state))}{kind}, "
            f"about {_interest(state).format()}/yr"
        )
    if [s.name for s in snowball] != [s.name for s in avalanche]:
        detail.append(f"Smallest balance first would instead run: {', '.join(s.name for s in snowball)}.")

    promo_window = thresholds.integer("debt", "promo_warning_days")
    ending: list[AccountState] = []
    for state in rated:
        assert state.terms is not None
        ends = state.terms.promo_ends_on
        if ends is None:
            continue
        days = (ends - snapshot.as_of).days
        if days < 0:
            assumptions.append(
                f"{state.name}'s promotional rate ended {ends}; confirm the current rate with `fa terms`."
            )
            continue
        if days > promo_window:
            continue
        ending.append(state)
        after = state.terms.post_promo_rate
        if after is None:
            detail.append(f"{state.name}: the promotional rate ends {ends} ({days} days); the rate after isn't recorded.")
            missing.append(f'The post-promotion rate for {state.name}: `fa terms --account "{state.name}" --post-promo-rate PERCENT`.')
        else:
            detail.append(
                f"{state.name}: the {fmt_rate(_rate(state))} promotional rate ends {ends} ({days} days), "
                f"then {fmt_rate(after)}, about {(_balance(state) * after).format()}/yr at this balance."
            )

    variable = [s for s in rated if s.terms is not None and s.terms.rate_kind == "variable"]
    facts = [
        Fact("Total interest-bearing debt", total.format()),
        Fact("Estimated interest", f"{interest.format()}/yr"),
        Fact("Weighted rate", fmt_rate(weighted)),
    ]
    if variable:
        facts.append(Fact("Variable rate", names(variable)))
    if snapshot.benchmark is not None:
        above = [s for s in rated if _rate(s) > snapshot.benchmark.rate]
        facts.append(
            Fact(
                f"Charging more than the {fmt_rate(snapshot.benchmark.rate)} Treasury bill rate",
                f"{len(above)} of {len(rated)}",
            )
        )
        assumptions.append(
            "Paying down a debt earns its interest rate with certainty, so rates are compared "
            "with the risk-free Treasury bill rate rather than an assumed market return."
        )
    if any(s.account.type_code == "mortgage" for s in rated):
        assumptions.append("Mortgage interest can be tax-deductible if you itemize; that isn't assessed.")
    note = stale_note(rated, snapshot.as_of)
    if note:
        assumptions.append(note)
    inputs = (f"Balances and terms: {names(rated)}",)

    severity = Severity.NONE
    if high:
        high_cutoff = thresholds.money("debt", "high_interest_annual")
        severity = Severity.HIGH if high_interest >= high_cutoff else Severity.MEDIUM
    if ending:
        severity = max(severity, Severity.MEDIUM)
    if severity is Severity.NONE and variable:
        severity = Severity.LOW

    if severity is not Severity.NONE:
        if high:
            summary = (
                f"{len(high)} debt(s) charge {fmt_rate(high_rate)} or more, costing about "
                f"{high_interest.format()} a year; {avalanche[0].name} has the highest rate at "
                f"{fmt_rate(_rate(avalanche[0]))}."
            )
        elif ending:
            summary = f"A promotional rate ends within {promo_window} days on {names(ending)}."
        else:
            summary = f"{names(variable)} carry variable rates, so their cost moves with interest rates."
        return [
            attention(
                KEY,
                TITLE,
                severity,
                summary,
                facts=facts,
                annual_impact=high_interest if high else None,
                detail=detail,
                inputs=inputs,
                assumptions=assumptions,
                missing=missing,
            )
        ]
    return [
        ok(
            KEY,
            TITLE,
            f"{len(rated)} debt(s) totaling {total.format()} at a weighted {fmt_rate(weighted)}; "
            f"none charges {fmt_rate(high_rate)} or more.",
            facts=facts,
            detail=detail,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    ]
