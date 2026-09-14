"""F3.5 — Asset location: tax-inefficient assets held in taxable accounts."""

from __future__ import annotations

from ...money import Money
from ..model import Fact, Observation, Severity
from ..portfolio import build_portfolio, effective_tax_treatment, portfolio_missing, portfolio_notes
from ..snapshot import Snapshot
from ._common import attention, insufficient, not_applicable, ok

TITLE = "Asset location"
KEY = "F3.5.location"


def check(snapshot: Snapshot) -> list[Observation]:
    portfolio = build_portfolio(snapshot)
    if portfolio is None:
        return [not_applicable(KEY, TITLE, "No investment accounts are recorded.")]

    classes = snapshot.rules.asset_classes
    inefficient = {k for k, c in classes.items() if c.tax_efficiency == "inefficient"}
    efficient = {k for k, c in classes.items() if c.tax_efficiency == "efficient"}

    inefficient_in_taxable = Money(0)
    efficient_in_deferred = Money(0)
    unclassified = Money(0)
    has_taxable = has_sheltered = False
    inferred: dict[str, str] = {}

    for exposure in portfolio.exposures:
        treatment, was_inferred = effective_tax_treatment(exposure.account)
        if was_inferred:
            inferred[exposure.account.name] = exposure.account.type_label
        if treatment == "taxable":
            has_taxable = True
        else:
            has_sheltered = True
        if not exposure.weights:
            unclassified += exposure.value
            continue
        for cls, weight in exposure.weights.items():
            part = exposure.value * weight
            if treatment == "taxable" and cls in inefficient:
                inefficient_in_taxable += part
            elif treatment == "tax_deferred" and cls in efficient:
                efficient_in_deferred += part

    # Applicability comes from the accounts that exist, not from the holdings that
    # happen to be imported: a 401(k) with no holdings yet is still a 401(k).
    treatments = {effective_tax_treatment(s.account)[0] for s in snapshot.investments()}
    if "taxable" not in treatments or treatments == {"taxable"}:
        return [
            not_applicable(
                KEY,
                TITLE,
                "Asset location only matters with both taxable and tax-advantaged "
                "investment accounts.",
            )
        ]
    missing = portfolio_missing(portfolio)
    one_side_unseen = not (has_taxable and has_sheltered)
    deferred_side_unseen = inefficient_in_taxable.cents > 0 and efficient_in_deferred.cents == 0
    if missing and (one_side_unseen or deferred_side_unseen):
        return [
            insufficient(
                KEY,
                TITLE,
                "Holdings are missing for some investment accounts, so asset location "
                "can't be assessed.",
                missing,
            )
        ]

    swappable = min(inefficient_in_taxable, efficient_in_deferred)
    inefficient_labels = ", ".join(classes[k].label for k in sorted(inefficient))
    facts = [
        Fact("Tax-inefficient assets in taxable accounts", inefficient_in_taxable.format()),
        Fact("Stock funds in tax-deferred accounts", efficient_in_deferred.format()),
    ]
    assumptions = [
        f"Tax-inefficient means {inefficient_labels}: returns that arrive mostly as ordinary "
        "income. It's a simplification — municipal bonds, for example, suit taxable accounts.",
        "Exchanging holdings inside a tax-advantaged account has no tax cost; selling in a "
        "taxable account can realize capital gains.",
        "Your marginal tax rate isn't known, so no dollar benefit is estimated.",
        "Roth accounts aren't counted as a place for bonds; their tax-free growth is "
        "conventionally used for higher-growth assets.",
        *portfolio_notes(portfolio),
    ]
    if inferred:
        listed = ", ".join(f"{name} ({label})" for name, label in sorted(inferred.items()))
        assumptions.append(
            f"Tax treatment was inferred from the account type for {listed}: recorded as taxable, "
            "which that type of account isn't."
        )
    if unclassified.cents:
        assumptions.append(f"{unclassified.format()} of unclassified holdings is left out.")
    inputs = ("Holdings by account", "Account tax treatment", "rules/asset_classes.yml")

    if swappable >= snapshot.thresholds.money("location", "min_swappable"):
        return [
            attention(
                KEY,
                TITLE,
                Severity.LOW,
                f"{inefficient_in_taxable.format()} of tax-inefficient assets sits in taxable "
                f"accounts while {efficient_in_deferred.format()} of stock funds sits in "
                f"tax-deferred accounts. Up to {swappable.format()} could be held the other way "
                "round with the same overall allocation.",
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
                missing=missing,
                professional_review=True,
            )
        ]
    return [
        ok(
            KEY,
            TITLE,
            "No meaningful amount of tax-inefficient assets in taxable accounts could trade "
            "places with stock funds in tax-deferred accounts.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            missing=missing,
        )
    ]
