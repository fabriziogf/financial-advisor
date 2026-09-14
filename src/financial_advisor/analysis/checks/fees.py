"""F3.6 — Fees: what the portfolio's funds cost, and cheaper equivalents on file."""

from __future__ import annotations

from ...money import Money
from ..model import Fact, Observation, Severity, fmt_pct, fmt_rate
from ..portfolio import Exposure, build_portfolio, portfolio_missing, portfolio_notes
from ..snapshot import Snapshot
from ._common import attention, insufficient, not_applicable, ok

TITLE = "Fees"
KEY = "F3.6.fees"

# Accounts whose fund menu is set by an employer or plan. Naming a cheaper fund the
# account can't actually hold would be a finding with no possible action behind it.
RESTRICTED_MENU_TYPES = frozenset({"retirement_401k", "hsa", "529"})


def _annual_cost(exposure: Exposure) -> Money:
    assert exposure.security is not None and exposure.security.expense_ratio is not None
    return exposure.value * exposure.security.expense_ratio


def check(snapshot: Snapshot) -> list[Observation]:
    portfolio = build_portfolio(snapshot)
    if portfolio is None:
        return [not_applicable(KEY, TITLE, "No investment accounts are recorded.")]
    missing = portfolio_missing(portfolio)
    known = [e for e in portfolio.exposures if e.security and e.security.expense_ratio is not None]
    known_value = sum((e.value for e in known), Money(0))
    if portfolio.total.cents <= 0 or known_value.cents <= 0:
        return [
            insufficient(
                KEY,
                TITLE,
                "No holdings have an expense ratio on file.",
                missing or ["Import holdings with `fa holdings FILE --account NAME`."],
            )
        ]

    thresholds = snapshot.thresholds
    high = thresholds.decimal("fees", "high_expense_ratio")
    min_saving = thresholds.money("fees", "min_annual_saving")
    catalog = snapshot.rules.securities

    cost = sum((_annual_cost(e) for e in known), Money(0))
    weighted = cost.ratio_to(known_value)
    facts = [
        Fact("Annual fund costs", f"{cost.format()}/yr"),
        Fact("Weighted expense ratio", fmt_rate(weighted)),
        Fact("Holdings with known costs", f"{fmt_pct(known_value.ratio_to(portfolio.total))} of the portfolio"),
    ]

    detail: list[str] = []
    savings = Money(0)
    expensive = 0
    for exposure in sorted(known, key=lambda e: (-_annual_cost(e).cents, e.symbol or "")):
        security = exposure.security
        assert security is not None and security.expense_ratio is not None
        ratio = security.expense_ratio
        restricted = exposure.account.type_code in RESTRICTED_MENU_TYPES
        alternative = None
        if security.category and not restricted:
            peers = [
                s
                for s in catalog.values()
                if s.category == security.category
                and s.symbol != security.symbol
                and s.expense_ratio is not None
                and s.expense_ratio < ratio
            ]
            if peers:
                alternative = min(peers, key=lambda s: (s.expense_ratio, s.symbol))
        saving = (
            exposure.value * (ratio - alternative.expense_ratio)
            if alternative is not None and alternative.expense_ratio is not None
            else Money(0)
        )
        is_high = ratio > high
        if not is_high and saving < min_saving:
            continue

        line = (
            f"{exposure.symbol} in {exposure.account.name}: {fmt_rate(ratio)}, about "
            f"{_annual_cost(exposure).format()}/yr"
        )
        if alternative is not None and saving >= min_saving:
            line += (
                f"; {alternative.symbol} in the same category costs "
                f"{fmt_rate(alternative.expense_ratio or ratio)}, about {saving.format()}/yr less"
            )
            savings += saving
        elif is_high and restricted:
            line += "; this account's fund menu is set by the plan"
        if is_high:
            expensive += 1
        detail.append(line + ".")

    assumptions = [
        "Cheaper alternatives come only from your securities catalog, and only for accounts "
        "where you choose the funds; workplace plans, HSAs, and 529s offer fixed menus.",
        "Selling a fund in a taxable account can realize capital gains; that cost isn't included.",
        "Only fund expense ratios are counted, not advisory or account fees. Ratios are as "
        "recorded in your catalog — confirm them against current fund documents.",
        *portfolio_notes(portfolio),
    ]
    inputs = ("Holdings and market values", "Expense ratios and categories in your securities catalog")

    if expensive or savings.cents:
        medium = thresholds.money("fees", "medium_annual_savings")
        severity = Severity.MEDIUM if savings >= medium or weighted > high else Severity.LOW
        if savings.cents:
            summary = (
                f"Fund costs are about {cost.format()} a year ({fmt_rate(weighted)} weighted); "
                f"lower-cost funds in the same categories would cost about {savings.format()} "
                "a year less."
            )
        else:
            summary = (
                f"{expensive} holding(s) charge more than {fmt_rate(high)}; fund costs total "
                f"about {cost.format()} a year."
            )
        return [
            attention(
                KEY,
                TITLE,
                severity,
                summary,
                facts=facts,
                annual_impact=savings if savings.cents else None,
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
            f"Fund costs are about {cost.format()} a year, a weighted {fmt_rate(weighted)}; no "
            f"holding charges more than {fmt_rate(high)}.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    ]
