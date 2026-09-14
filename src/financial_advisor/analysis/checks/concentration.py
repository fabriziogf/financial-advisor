"""F3.7 — Concentration: single holdings, and employer stock in particular.

Employer stock gets its own, stricter treatment. It is the one holding whose risk
is correlated with your income: a bad year for the company can cost the job and the
savings together.
"""

from __future__ import annotations

from decimal import Decimal

from ...rules import LOCAL_SECURITIES_FILENAME
from ..model import Fact, Observation, Severity, fmt_pct
from ..portfolio import build_portfolio, portfolio_notes
from ..snapshot import Snapshot
from ._common import attention, insufficient, not_applicable, ok

TITLE = "Concentration"
KEY = "F3.7.concentration"


def check(snapshot: Snapshot) -> list[Observation]:
    portfolio = build_portfolio(snapshot)
    if portfolio is None:
        return [not_applicable(KEY, TITLE, "No investment accounts are recorded.")]
    if portfolio.total.cents <= 0:
        return [
            insufficient(
                KEY,
                TITLE,
                "Investment accounts have no holdings or balances recorded.",
                ["Import holdings with `fa holdings FILE --account NAME`."],
            )
        ]

    thresholds = snapshot.thresholds
    single = thresholds.decimal("concentration", "single_position")
    employer_limit = thresholds.decimal("concentration", "employer_stock")
    employer_high = thresholds.decimal("concentration", "employer_stock_high")
    profile = snapshot.profile
    employer = (
        profile.employer_plan.employer_stock_symbol
        if profile is not None and profile.employer_plan is not None
        else None
    )
    catalog = snapshot.rules.securities

    findings: list[tuple[Severity, str]] = []
    unassessed: list[tuple[str, Decimal]] = []
    facts = [Fact("Portfolio", portfolio.total.format())]

    holdings = sorted(portfolio.by_symbol().items(), key=lambda item: (-item[1].cents, item[0]))
    for symbol, value in holdings:
        share = value.ratio_to(portfolio.total)
        if symbol == employer:
            facts.append(Fact(f"{symbol} (employer stock)", f"{fmt_pct(share)} ({value.format()})"))
            if share > employer_limit:
                findings.append(
                    (
                        Severity.HIGH if share > employer_high else Severity.MEDIUM,
                        f"{symbol}, your employer's stock, is {fmt_pct(share)} of the portfolio "
                        f"({value.format()}). A downturn at the company could affect your job "
                        "and these savings at the same time.",
                    )
                )
            continue
        if share <= single:
            continue
        security = catalog.get(symbol)
        if security is None or security.diversified is None:
            unassessed.append((symbol, share))
        elif security.diversified is False:
            findings.append(
                (
                    Severity.MEDIUM if share > single * 2 else Severity.LOW,
                    f"{symbol} is {fmt_pct(share)} of the portfolio ({value.format()}).",
                )
            )

    missing = [
        f"Whether {symbol} is diversified: set `diversified: true` or `false` for it in "
        f"{LOCAL_SECURITIES_FILENAME}. It is {fmt_pct(share)} of the portfolio."
        for symbol, share in unassessed
    ]
    assumptions = [
        f"Diversified funds are never flagged; any other single holding above {fmt_pct(single)} is.",
        "Sector and industry concentration aren't assessed; the securities catalog has no sector data.",
        "Only holdings in recorded investment accounts count. Unvested equity, or shares held "
        "somewhere not recorded, aren't included.",
        *portfolio_notes(portfolio),
    ]
    if portfolio.without_holdings:
        listed = ", ".join(name for name, _ in portfolio.without_holdings)
        assumptions.append(f"Accounts without imported holdings can't be checked: {listed}.")
    if employer is None:
        assumptions.append(
            "No employer stock symbol is set (employer_plan.employer_stock_symbol), so "
            "employer-stock exposure isn't checked."
        )
    inputs = ("Holdings and market values", "`diversified` flags in your securities catalog")

    if findings:
        findings.sort(key=lambda f: -f[0])
        summary = (
            findings[0][1]
            if len(findings) == 1
            else f"{len(findings)} holdings are concentrated enough to note; the largest risk: {findings[0][1]}"
        )
        return [
            attention(
                KEY,
                TITLE,
                findings[0][0],
                summary,
                facts=facts,
                detail=[text for _, text in findings],
                inputs=inputs,
                assumptions=assumptions,
                missing=missing,
            )
        ]
    if unassessed:
        return [
            insufficient(
                KEY,
                TITLE,
                "Some large holdings aren't marked as diversified or not, so they can't be assessed.",
                missing,
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]
    return [
        ok(
            KEY,
            TITLE,
            f"No single non-diversified holding exceeds {fmt_pct(single)} of the portfolio.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
        )
    ]
