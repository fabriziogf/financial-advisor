"""F3.2 — Cash drag: cash earning materially less than a risk-free benchmark."""

from __future__ import annotations

from ...money import Money
from ...rates import SERIES_LABEL
from ..model import Fact, Observation, Severity, fmt_rate
from ..snapshot import LIQUID_TYPES, Snapshot
from ._common import attention, insufficient, names, not_applicable, ok, stale_note

TITLE = "Cash drag"
KEY = "F3.2.yield"


def check(snapshot: Snapshot) -> list[Observation]:
    thresholds = snapshot.thresholds
    cash = snapshot.of_types(LIQUID_TYPES)
    if not cash:
        return [not_applicable(KEY, TITLE, "No checking, savings, or money market accounts are recorded.")]
    if snapshot.benchmark is None:
        return [
            insufficient(
                KEY,
                TITLE,
                "No benchmark rate is cached, so cash yields can't be compared with anything.",
                ["Fetch the 3-month Treasury bill rate: `fa rates refresh`."],
            )
        ]

    benchmark = snapshot.benchmark.rate
    tolerance = thresholds.decimal("cash_drag", "yield_shortfall_tolerance")
    operating_months = thresholds.decimal("cash_drag", "checking_operating_months")
    basis = snapshot.expense_basis()

    funded = [s for s in cash if s.balance is not None and s.balance.cents > 0]
    no_rate = [s for s in funded if s.terms is None or s.terms.rate is None]
    rated = [s for s in funded if s.terms is not None and s.terms.rate is not None]
    unbalanced = [s for s in cash if s.balance is None]

    missing: list[str] = []
    if no_rate:
        missing.append(
            f"Interest rates for {names(no_rate)}: `fa terms --account NAME --rate 4.25` "
            "(the APY as a percent; use 0 for an account that pays nothing)."
        )
    if unbalanced:
        missing.append(f"Balances for {names(unbalanced)}.")
    if not rated:
        return [
            insufficient(
                KEY,
                TITLE,
                "No cash account has both a balance and an interest rate recorded.",
                missing or ["Record balances for your cash accounts with `fa balance`."],
            )
        ]

    assumptions = [
        "The benchmark is the 3-month Treasury bill rate. Treasury interest is exempt from "
        "state income tax and bank interest is not, so the after-tax gap can be wider.",
        "Balances and rates are treated as unchanged for a year.",
        f"A yield within {fmt_rate(tolerance)} of the benchmark isn't counted.",
    ]
    detail: list[str] = []
    foregone = Money(0)
    flagged = 0
    checking_unexempted = False

    for state in rated:
        assert state.balance is not None and state.terms is not None and state.terms.rate is not None
        rate = state.terms.rate
        exempt = Money(0)
        if state.account.type_code == "checking":
            if basis is not None:
                exempt = basis.amount * operating_months
            else:
                checking_unexempted = True
        idle = state.balance - exempt
        gap = benchmark - rate
        if idle.cents <= 0 or gap <= tolerance:
            continue
        cost = idle * gap
        foregone += cost
        flagged += 1
        kept = f" after keeping {exempt.format()} for bills" if exempt else ""
        detail.append(
            f"{state.name}: {idle.format()}{kept} earns {fmt_rate(rate)} against "
            f"{fmt_rate(benchmark)}, about {cost.format()}/yr less."
        )

    if checking_unexempted:
        assumptions.append(
            "Checking balances are counted in full because monthly spending is unknown; in "
            "practice some cash there covers bills."
        )
    elif basis is not None:
        assumptions.append(
            f"Checking accounts keep {operating_months} month(s) of expenses "
            f"({(basis.amount * operating_months).format()}) out of the calculation for bills."
        )
    age = (snapshot.as_of - snapshot.benchmark.observed_on).days
    if age > thresholds.integer("staleness", "benchmark_rate_days"):
        assumptions.append(
            f"The benchmark rate is from {snapshot.benchmark.observed_on} ({age} days old); "
            "run `fa rates refresh`."
        )
    note = stale_note(rated, snapshot.as_of)
    if note:
        assumptions.append(note)

    facts = [
        Fact("Benchmark", f"{fmt_rate(benchmark)} ({SERIES_LABEL}, {snapshot.benchmark.observed_on})"),
        Fact("Accounts compared", str(len(rated))),
    ]
    liquid_total = snapshot.liquid_total()
    if basis is not None and liquid_total is not None:
        target, _ = snapshot.emergency_target()
        excess = liquid_total - basis.amount * target
        if excess.cents > 0:
            facts.append(Fact("Cash above emergency-fund target", excess.format()))

    inputs = (f"Rates and balances: {names(rated)}", f"Benchmark: {SERIES_LABEL}")
    floor = thresholds.money("cash_drag", "attention_annual_impact")
    if foregone >= floor:
        severity = (
            Severity.MEDIUM
            if foregone >= thresholds.money("cash_drag", "medium_annual_impact")
            else Severity.LOW
        )
        return [
            attention(
                KEY,
                TITLE,
                severity,
                f"{flagged} account(s) earn well below the {fmt_rate(benchmark)} Treasury bill "
                f"rate: about {foregone.format()} a year in interest not earned at current balances.",
                facts=facts,
                annual_impact=foregone,
                detail=detail,
                inputs=inputs,
                assumptions=assumptions,
                missing=missing,
            )
        ]

    summary = (
        f"Cash yields are close to the {fmt_rate(benchmark)} benchmark."
        if not foregone
        else f"Cash yields trail the benchmark by about {foregone.format()} a year in total, "
        f"under the {floor.format()} threshold for attention."
    )
    return [
        ok(
            KEY,
            TITLE,
            summary,
            facts=facts,
            annual_impact=foregone if foregone else None,
            detail=detail,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    ]
