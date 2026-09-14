"""The investment portfolio as the holdings-based checks see it (F3.4–F3.7).

Pure. Built from a snapshot; shared by allocation, location, fees, and
concentration so all four agree on what the portfolio *is*.

Two rules shape it:

* **An account with a balance but no imported holdings still counts** — as an
  unclassified exposure at its balance. Dropping it would shrink the portfolio and
  inflate every percentage computed from what remains.
* **A position without a market value is excluded and listed**, never valued at zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..db.store import Account
from ..money import Money
from ..reports.net_worth import STALE_AFTER_DAYS
from ..rules import LOCAL_SECURITIES_FILENAME, SecurityInfo
from .snapshot import Snapshot

__all__ = [
    "Exposure",
    "Portfolio",
    "build_portfolio",
    "effective_tax_treatment",
    "portfolio_missing",
    "portfolio_notes",
]

# Account types whose tax treatment follows from the type. M0's `account-add`
# defaults every account to "taxable", so a 401(k) added without --tax would
# otherwise read as taxable and corrupt the asset-location analysis.
_TAX_TREATMENT_BY_TYPE = {
    "retirement_401k": "tax_deferred",
    "ira_traditional": "tax_deferred",
    "ira_roth": "tax_free",
    "hsa": "tax_free",
    "529": "tax_free",
}


def effective_tax_treatment(account: Account) -> tuple[str, bool]:
    """(treatment, inferred). A stated non-default treatment is always respected —
    a Roth 401(k) recorded as tax_free stays tax_free."""
    inferred = _TAX_TREATMENT_BY_TYPE.get(account.type_code)
    if inferred and account.tax_treatment == "taxable":
        return inferred, True
    return account.tax_treatment, False


@dataclass(frozen=True)
class Exposure:
    account: Account
    symbol: str | None            # None: an account valued by balance, holdings unknown
    value: Money
    security: SecurityInfo | None

    @property
    def weights(self) -> dict[str, Decimal]:
        return self.security.weights if self.security else {}


@dataclass(frozen=True)
class Portfolio:
    total: Money
    exposures: tuple[Exposure, ...]
    unvalued: tuple[tuple[str, str], ...]              # (account, symbol)
    without_holdings: tuple[tuple[str, Money], ...]    # (account, balance used)
    without_data: tuple[str, ...]                      # neither balance nor holdings
    mismatches: tuple[tuple[str, Money, Money], ...]   # (account, holdings total, balance)
    stale: tuple[tuple[str, date], ...]                # (account, holdings date)

    def by_asset_class(self) -> dict[str | None, Money]:
        """Look-through values per asset class. Key None collects the unclassified."""
        result: dict[str | None, Money] = {}
        for exposure in self.exposures:
            if not exposure.weights:
                result[None] = result.get(None, Money(0)) + exposure.value
                continue
            for cls, weight in exposure.weights.items():
                result[cls] = result.get(cls, Money(0)) + exposure.value * weight
        return result

    def by_symbol(self) -> dict[str, Money]:
        result: dict[str, Money] = {}
        for exposure in self.exposures:
            if exposure.symbol is not None:
                result[exposure.symbol] = result.get(exposure.symbol, Money(0)) + exposure.value
        return result

    @property
    def unknown_symbols(self) -> tuple[str, ...]:
        return tuple(sorted({e.symbol for e in self.exposures if e.symbol and e.security is None}))

    @property
    def unweighted_symbols(self) -> tuple[str, ...]:
        return tuple(
            sorted({e.symbol for e in self.exposures if e.symbol and e.security and not e.weights})
        )


def build_portfolio(snapshot: Snapshot) -> Portfolio | None:
    investments = snapshot.investments()
    if not investments:
        return None
    catalog = snapshot.rules.securities
    tolerance = snapshot.thresholds.decimal("allocation", "balance_mismatch_tolerance")

    exposures: list[Exposure] = []
    unvalued: list[tuple[str, str]] = []
    without_holdings: list[tuple[str, Money]] = []
    without_data: list[str] = []
    mismatches: list[tuple[str, Money, Money]] = []
    stale: list[tuple[str, date]] = []

    for state in investments:
        if state.positions:
            holdings_total = Money(0)
            for position in state.positions:
                if position.market_value is None:
                    unvalued.append((state.name, position.symbol))
                    continue
                holdings_total += position.market_value
                exposures.append(
                    Exposure(state.account, position.symbol, position.market_value, catalog.get(position.symbol))
                )
            held_on = state.positions_as_of
            # Only comparable on the same day; otherwise the market moved in between.
            if state.balance is not None and held_on == state.balance_as_of and holdings_total.cents:
                gap = abs(abs(state.balance) - holdings_total)
                if gap.ratio_to(holdings_total) > tolerance:
                    mismatches.append((state.name, holdings_total, abs(state.balance)))
            if held_on and (snapshot.as_of - held_on).days > STALE_AFTER_DAYS:
                stale.append((state.name, held_on))
        elif state.balance is not None and state.balance.cents != 0:
            exposures.append(Exposure(state.account, None, abs(state.balance), None))
            without_holdings.append((state.name, abs(state.balance)))
        else:
            without_data.append(state.name)

    return Portfolio(
        total=sum((e.value for e in exposures), Money(0)),
        exposures=tuple(exposures),
        unvalued=tuple(unvalued),
        without_holdings=tuple(without_holdings),
        without_data=tuple(without_data),
        mismatches=tuple(mismatches),
        stale=tuple(stale),
    )


def portfolio_missing(portfolio: Portfolio) -> list[str]:
    """What to provide so the holdings-based checks can see the whole portfolio."""
    missing: list[str] = []
    if portfolio.without_holdings:
        names = ", ".join(name for name, _ in portfolio.without_holdings)
        missing.append(
            f"Holdings for {names}: `fa holdings FILE --account NAME`. "
            "Until then each counts as unclassified at its balance."
        )
    if portfolio.without_data:
        missing.append(f"A balance or holdings for {', '.join(portfolio.without_data)}.")
    if portfolio.unknown_symbols:
        missing.append(
            f"Describe {', '.join(portfolio.unknown_symbols)} in {LOCAL_SECURITIES_FILENAME} "
            "(asset_class, expense_ratio, category, diversified). It lives in your data "
            "directory, not the repo — the list of funds you hold is itself private."
        )
    if portfolio.unweighted_symbols:
        missing.append(
            f"Asset-class weights for {', '.join(portfolio.unweighted_symbols)} in your securities catalog."
        )
    if portfolio.unvalued:
        pairs = ", ".join(f"{symbol} in {name}" for name, symbol in portfolio.unvalued)
        missing.append(f"Market values for {pairs}; unvalued positions are left out.")
    return missing


def portfolio_notes(portfolio: Portfolio) -> list[str]:
    notes: list[str] = []
    for name, holdings_total, balance in portfolio.mismatches:
        notes.append(
            f"Holdings for {name} total {holdings_total.format()} but its balance on the same "
            f"day is {balance.format()}; figures use the holdings."
        )
    for name, held_on in portfolio.stale:
        notes.append(f"Holdings for {name} are from {held_on}; prices have moved since.")
    return notes
