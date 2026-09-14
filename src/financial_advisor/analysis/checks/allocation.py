"""F3.4 — Asset allocation against your target."""

from __future__ import annotations

from decimal import Decimal

from ...money import Money
from ..model import Fact, Observation, Severity, fmt_pct, fmt_points
from ..portfolio import build_portfolio, portfolio_missing, portfolio_notes
from ..snapshot import Snapshot
from ._common import attention, insufficient, not_applicable, ok

TITLE = "Asset allocation"
KEY = "F3.4.drift"


def check(snapshot: Snapshot) -> list[Observation]:
    portfolio = build_portfolio(snapshot)
    if portfolio is None:
        return [not_applicable(KEY, TITLE, "No investment accounts are recorded.")]
    thresholds = snapshot.thresholds
    classes = snapshot.rules.asset_classes
    missing = portfolio_missing(portfolio)
    if portfolio.total.cents <= 0:
        return [
            insufficient(
                KEY,
                TITLE,
                "Investment accounts have no holdings or balances recorded.",
                missing or ["Import holdings with `fa holdings FILE --account NAME`."],
            )
        ]

    by_class = portfolio.by_asset_class()
    unclassified = by_class.pop(None, Money(0))
    classified: dict[str, Money] = {
        cls: value for cls, value in by_class.items() if cls is not None
    }
    classified_total = portfolio.total - unclassified
    unclassified_share = unclassified.ratio_to(portfolio.total)

    facts = [Fact("Portfolio", portfolio.total.format())]
    if classified_total.cents > 0:
        for cls, value in sorted(classified.items(), key=lambda item: (-item[1].cents, item[0])):
            share = value.ratio_to(classified_total)
            facts.append(Fact(classes[cls].label, f"{fmt_pct(share)} ({value.format()})"))
    if unclassified.cents:
        facts.append(
            Fact(
                "Unclassified",
                f"{fmt_pct(unclassified_share)} of the portfolio ({unclassified.format()})",
            )
        )

    assumptions = [
        "The portfolio is investment accounts only; cash accounts, including the emergency "
        "fund, are excluded.",
        "Blended funds are split into asset classes using the look-through weights in your "
        "securities catalog.",
        *portfolio_notes(portfolio),
    ]
    inputs = (
        "Holdings and balances of investment accounts",
        "Securities catalog (rules/securities.yml plus your local overlay)",
    )

    too_unclassified = unclassified_share > thresholds.decimal(
        "allocation", "unclassified_insufficient"
    )
    if classified_total.cents <= 0 or too_unclassified:
        return [
            insufficient(
                KEY,
                TITLE,
                f"{fmt_pct(unclassified_share)} of the portfolio can't be classified by asset "
                "class, so allocation can't be assessed.",
                missing,
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]
    if unclassified.cents:
        assumptions.append(
            f"Percentages are shares of the classified {classified_total.format()}; the "
            f"unclassified {unclassified.format()} is left out."
        )
    if unclassified_share > thresholds.decimal("allocation", "unclassified_low_confidence"):
        assumptions.append(
            "A sizeable share is unclassified, so treat drift figures as approximate."
        )

    profile = snapshot.profile
    target = profile.target_allocation if profile else None
    if not target:
        missing.append(
            "Set investments.target_allocation_percent in your profile to compare against a target."
        )
        return [
            insufficient(
                KEY,
                TITLE,
                "Your current allocation is shown, but there's no target to compare it with.",
                missing,
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
            )
        ]

    if profile is not None and profile.drift_tolerance is not None:
        tolerance = profile.drift_tolerance
    else:
        tolerance = thresholds.decimal("allocation", "default_drift_tolerance")
        assumptions.append(
            f"The ±{fmt_pct(tolerance)} tolerance is the default in rules/thresholds.yml; set "
            "investments.drift_tolerance_percent to use your own."
        )

    drifts: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for cls in sorted(set(target) | set(classified)):
        actual = classified.get(cls, Money(0)).ratio_to(classified_total)
        goal = target.get(cls, Decimal(0))
        drifts.append((cls, actual, goal, actual - goal))
    detail = [
        f"{classes[cls].label}: {fmt_pct(actual)} against a {fmt_pct(goal)} target "
        f"({fmt_points(delta)}, about {(classified_total * abs(delta)).format()})"
        for cls, actual, goal, delta in drifts
    ]
    facts.append(Fact("Drift tolerance", f"±{fmt_pct(tolerance)}"))
    inputs = (*inputs, "investments.target_allocation_percent")

    cls, actual, goal, delta = max(drifts, key=lambda d: (abs(d[3]), d[0]))
    label = classes[cls].label
    if abs(delta) > tolerance:
        severity = Severity.MEDIUM if abs(delta) > tolerance * 2 else Severity.LOW
        direction = "above" if delta > 0 else "below"
        dollars = (classified_total * abs(delta)).format()
        return [
            attention(
                KEY,
                TITLE,
                severity,
                f"{label} at {fmt_pct(actual)} against a {fmt_pct(goal)} target: "
                f"{fmt_points(delta)}, about {dollars} {direction} target and outside the "
                f"±{fmt_pct(tolerance)} tolerance.",
                facts=facts,
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
            f"Every asset class is within ±{fmt_pct(tolerance)} of target; the largest gap is "
            f"{label} at {fmt_points(delta)}.",
            facts=facts,
            detail=detail,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    ]
