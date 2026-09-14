"""The observation checks, F3.1–F3.10, against hand-built snapshots.

Every expected figure is worked out by hand in the test (R1). No database, files, or
clock: each Snapshot is constructed directly.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path

import pytest

from financial_advisor.analysis import engine
from financial_advisor.analysis.cashflow import CashFlowSummary
from financial_advisor.analysis.checks import (
    allocation,
    cash_drag,
    concentration,
    debt,
    emergency_fund,
    fees,
    insurance,
    location,
    savings,
    tax_advantaged,
)
from financial_advisor.analysis.model import Observation, Severity, Status
from financial_advisor.analysis.portfolio import default_tax_treatment
from financial_advisor.analysis.snapshot import AccountState, Snapshot
from financial_advisor.db.store import Account, PositionRow, Terms
from financial_advisor.money import Money
from financial_advisor.profile import EmployerPlan, HSAEnrollment, MatchTier, Profile
from financial_advisor.rates import BenchmarkRate
from financial_advisor.reports.observations import format_observations
from financial_advisor.rules import (
    Rules,
    SecurityInfo,
    load_asset_classes,
    load_limits,
    load_securities,
    load_thresholds,
)

AS_OF = date(2026, 9, 13)
_CLASSES = load_asset_classes()
# Built with no local overlay on purpose: loading rules the normal way reads
# securities.local.yml from the data directory, which holds personal data.
RULES = Rules(
    thresholds=load_thresholds(),
    asset_classes=_CLASSES,
    securities=load_securities(_CLASSES, local_path=Path("/nonexistent/securities.local.yml")),
    limits=load_limits(2026),
)

_TYPES = {
    "checking": ("Checking", False, False),
    "savings": ("Savings", False, False),
    "credit_card": ("Credit Card", True, False),
    "brokerage": ("Taxable Brokerage", False, True),
    "retirement_401k": ("401(k)", False, True),
    "ira_roth": ("Roth IRA", False, True),
    "mortgage": ("Mortgage", True, False),
    "auto_loan": ("Auto Loan", True, False),
}
_ids = count(1)


def acct(
    name,
    type_code,
    balance=None,
    *,
    rate=None,
    kind=None,
    revolving=None,
    promo_ends=None,
    post_promo=None,
    positions=(),
    tax=None,
):
    label, liability, investment = _TYPES[type_code]
    account_id = next(_ids)
    account = Account(
        id=account_id,
        name=name,
        type_code=type_code,
        type_label=label,
        is_liability=liability,
        is_manual=False,
        institution=None,
        tax_treatment=tax or default_tax_treatment(type_code),
        is_investment=investment,
    )
    terms = None
    if any(v is not None for v in (rate, kind, revolving, promo_ends, post_promo)):
        terms = Terms(
            account_id,
            Decimal(rate) if rate is not None else None,
            kind,
            None,
            promo_ends,
            Decimal(post_promo) if post_promo is not None else None,
            revolving,
            AS_OF,
        )
    held = tuple(PositionRow(account_id, s, None, Money(v), AS_OF) for s, v in positions)
    return AccountState(
        account,
        Money(balance) if balance is not None else None,
        AS_OF if balance is not None else None,
        terms,
        held,
    )


def snap(
    *accounts,
    profile=None,
    spending=None,
    income="0",
    days=120,
    benchmark="0.0386",
    observed=date(2026, 9, 10),
    rules=RULES,
):
    flow = None
    if spending is not None:
        flow = CashFlowSummary(
            monthly_spending=Money(spending),
            monthly_income=Money(income),
            first_date=AS_OF - timedelta(days=days - 1),
            last_date=AS_OF,
            shortest_coverage_days=days,
            coverage=(),
            paired_transfer_count=0,
            paired_transfer_total=Money(0),
            unpaired_transfers_out=Money(0),
            unpaired_transfers_in=Money(0),
            unpaired_payments_counted=Money(0),
        )
    bench = BenchmarkRate("DTB3", observed, Decimal(benchmark), AS_OF) if benchmark else None
    return Snapshot(AS_OF, tuple(accounts), rules, profile, bench, flow)


def only(observations, key=None) -> Observation:
    matches = [o for o in observations if key is None or o.key == key]
    assert len(matches) == 1, [o.key for o in observations]
    return matches[0]


def fact(observation, label) -> str:
    for item in observation.facts:
        if item.label == label:
            return item.value
    raise AssertionError(f"no fact {label!r}; have {[f.label for f in observation.facts]}")


TIERS = (MatchTier(Decimal("1"), Decimal("0.03")), MatchTier(Decimal("0.5"), Decimal("0.05")))


def worker(salary="96000", contribution="0.03", birth_year=1986, *, stock=None, **fields):
    """100% of the first 3%, 50% of the next 2%: the full match is 4% of pay at a 5% deferral."""
    base = dict(
        birth_year=birth_year,
        annual_salary=Money(salary),
        employer_plan_stated=True,
        employer_plan=EmployerPlan(Decimal(contribution), TIERS, stock),
        ira_contributed=Money(0),
        hsa_stated=True,
        hsa=None,
    )
    base.update(fields)
    return Profile(**base)


def security(symbol, ratio="0", *, diversified=False, category=None, cls="us_equity"):
    return SecurityInfo(
        symbol, symbol, Decimal(ratio), {cls: Decimal(1)}, category, diversified, "local"
    )


def rules_with(*extra):
    return dataclasses.replace(
        RULES, securities={**RULES.securities, **{s.symbol: s for s in extra}}
    )


# --------------------------------------------------------------------------
class TestEmergencyFund:
    def test_target_met(self):
        """24,200 / 3,000 = 8.07 months ≥ 6."""
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", "6200"),
                    acct("Savings", "savings", "18000"),
                    spending="3000",
                    profile=Profile(emergency_target_months=Decimal(6)),
                )
            )
        )
        assert o.status is Status.OK
        assert fact(o, "Months covered") == "8.1"
        assert fact(o, "Above target") == "$6,200.00"  # 24,200 − 6 × 3,000

    @pytest.mark.parametrize(
        "cash, severity",
        [("20000", Severity.LOW), ("5000", Severity.MEDIUM), ("2000", Severity.HIGH)],
    )
    def test_severity_scales_with_depth(self, cash, severity):
        """Spending 4,000; target 6: 5 months → LOW, 1.25 (< 3) → MEDIUM, 0.5 (< 1) → HIGH."""
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", cash),
                    spending="4000",
                    profile=Profile(emergency_target_months=Decimal(6)),
                )
            )
        )
        assert o.status is Status.ATTENTION and o.severity is severity

    def test_shortfall_amount(self):
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", "5000"),
                    spending="4000",
                    profile=Profile(emergency_target_months=Decimal(6)),
                )
            )
        )
        assert fact(o, "Shortfall") == "$19,000.00"  # 24,000 − 5,000

    def test_unknown_balance_is_insufficient_not_a_partial_sum(self):
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", "6200"),
                    acct("Savings", "savings"),
                    spending="3000",
                )
            )
        )
        assert o.status is Status.INSUFFICIENT_DATA
        assert any("Savings" in m for m in o.missing)

    def test_declared_essential_expenses_override_transactions(self):
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", "24200"),
                    spending="3000",
                    profile=Profile(
                        monthly_essential_expenses=Money("2000"), emergency_target_months=Decimal(6)
                    ),
                )
            )
        )
        assert fact(o, "Months covered") == "12.1"

    def test_variable_income_raises_the_default_target(self):
        """No target set, variable income → 9 months; 24,200 / 3,000 = 8.07 < 9."""
        o = only(
            emergency_fund.check(
                snap(
                    acct("Checking", "checking", "24200"),
                    spending="3000",
                    profile=Profile(income_stability="variable"),
                )
            )
        )
        assert o.status is Status.ATTENTION
        assert "9-month" in o.summary
        assert any("rules/thresholds.yml" in a for a in o.assumptions)


# --------------------------------------------------------------------------
class TestCashDrag:
    def test_foregone_interest(self):
        """Benchmark 3.86%, tolerance 0.50pp, checking keeps 1 month (3,000) for bills.

        checking 3,200 idle × 3.86%           = 123.52
        savings 18,000 × (3.86% − 0.50%)      = 604.80
                                               = 728.32  (≥ 100, < 1,000 → LOW)
        """
        o = only(
            cash_drag.check(
                snap(
                    acct("Checking", "checking", "6200", rate="0"),
                    acct("Savings", "savings", "18000", rate="0.005"),
                    spending="3000",
                )
            )
        )
        assert o.status is Status.ATTENTION and o.severity is Severity.LOW
        assert o.annual_impact == Money("728.32")

    def test_checking_counted_in_full_when_spending_unknown(self):
        """6,200 × 3.86% = 239.32."""
        o = only(cash_drag.check(snap(acct("Checking", "checking", "6200", rate="0"))))
        assert o.annual_impact == Money("239.32")
        assert any("counted in full" in a for a in o.assumptions)

    def test_yield_within_tolerance_is_not_counted(self):
        """Savings at 3.40% trails by 0.46pp ≤ 0.50pp; only checking's 123.52 counts."""
        o = only(
            cash_drag.check(
                snap(
                    acct("Checking", "checking", "6200", rate="0"),
                    acct("Savings", "savings", "18000", rate="0.034"),
                    spending="3000",
                )
            )
        )
        assert o.annual_impact == Money("123.52")

    def test_medium_severity(self):
        """40,000 × 3.86% = 1,544.00 ≥ 1,000."""
        o = only(cash_drag.check(snap(acct("Savings", "savings", "40000", rate="0"))))
        assert o.severity is Severity.MEDIUM and o.annual_impact == Money("1544.00")

    def test_small_drag_is_ok_but_still_quantified(self):
        """1,000 × 3.86% = 38.60 < 100."""
        o = only(cash_drag.check(snap(acct("Savings", "savings", "1000", rate="0"))))
        assert o.status is Status.OK and o.annual_impact == Money("38.60")

    def test_missing_rate_is_named_while_others_are_assessed(self):
        o = only(
            cash_drag.check(
                snap(
                    acct("Checking", "checking", "6200", rate="0"),
                    acct("Savings", "savings", "18000"),
                )
            )
        )
        assert o.annual_impact == Money("239.32")
        assert any("Savings" in m for m in o.missing)

    def test_no_benchmark_is_insufficient(self):
        o = only(
            cash_drag.check(snap(acct("Savings", "savings", "1000", rate="0"), benchmark=None))
        )
        assert o.status is Status.INSUFFICIENT_DATA
        assert "fa rates refresh" in o.missing[0]

    def test_stale_benchmark_is_disclosed(self):
        o = only(
            cash_drag.check(
                snap(
                    acct("Savings", "savings", "40000", rate="0"),
                    observed=date(2026, 8, 1),
                )
            )
        )
        assert any("fa rates refresh" in a for a in o.assumptions)


# --------------------------------------------------------------------------
class TestTaxAdvantaged:
    def test_unclaimed_match(self):
        """96,000 × (4% − 3%) = 960 unclaimed → MEDIUM (≥ 500, < 2,000)."""
        o = only(tax_advantaged.check(snap(profile=worker())), "F3.3.match")
        assert o.status is Status.ATTENTION and o.severity is Severity.MEDIUM
        assert o.annual_impact == Money("960.00")
        assert fact(o, "Match earned") == "$2,880.00/yr"
        assert fact(o, "Full match available") == "$3,840.00/yr"

    def test_full_match(self):
        o = only(tax_advantaged.check(snap(profile=worker(contribution="0.05"))), "F3.3.match")
        assert o.status is Status.OK

    def test_high_severity(self):
        """200,000 × 4% = 8,000 unclaimed at a 0% deferral → HIGH."""
        o = only(
            tax_advantaged.check(snap(profile=worker(salary="200000", contribution="0"))),
            "F3.3.match",
        )
        assert o.severity is Severity.HIGH and o.annual_impact == Money("8000.00")

    def test_match_uses_the_compensation_limit(self):
        """500,000 salary → only 360,000 counts: 4% = 14,400, not 20,000."""
        o = only(
            tax_advantaged.check(snap(profile=worker(salary="500000", contribution="0.05"))),
            "F3.3.match",
        )
        assert fact(o, "Full match available") == "$14,400.00/yr"

    def test_limit_reached_early_can_cut_the_match(self):
        """400,000 at 10% = 40,000 elected > 24,500 limit.
        Match 14,400 (capped pay). If matching stops at the limit:
        14,400 × 24,500 / 40,000 = 8,820."""
        results = tax_advantaged.check(snap(profile=worker(salary="400000", contribution="0.10")))
        match = only(results, "F3.3.match")
        assert fact(match, "Match if contributions stop at the limit") == "$8,820.00/yr"
        deferral = only(results, "F3.3.deferral")
        assert deferral.status is Status.ATTENTION and "$15,500.00" in deferral.summary

    def test_catch_up_at_60(self):
        o = only(
            tax_advantaged.check(
                snap(profile=worker(salary="100000", contribution="0.05", birth_year=1966))
            ),
            "F3.3.deferral",
        )
        assert fact(o, "2026 limit") == "$35,750.00"

    def test_roth_catch_up_rule_flags_review(self):
        o = only(
            tax_advantaged.check(snap(profile=worker(salary="200000", birth_year=1971))),
            "F3.3.deferral",
        )
        assert o.professional_review
        assert any("Roth" in a for a in o.assumptions)

    def test_ira_excess(self):
        o = only(
            tax_advantaged.check(snap(profile=worker(ira_contributed=Money("8000")))), "F3.3.ira"
        )
        assert o.severity is Severity.MEDIUM and o.professional_review
        assert "$500.00" in o.summary

    def test_hsa_room_with_family_catch_up(self):
        """Family 8,750 + 1,000 at age 56 = 9,750; 9,750 − 5,000 = 4,750."""
        profile = worker(birth_year=1970, hsa=HSAEnrollment("family", Money("5000")))
        o = only(tax_advantaged.check(snap(profile=profile)), "F3.3.hsa")
        assert fact(o, "Unused room") == "$4,750.00"

    def test_not_stated_versus_none(self):
        unstated = tax_advantaged.check(snap(profile=Profile()))
        assert only(unstated, "F3.3.match").status is Status.INSUFFICIENT_DATA
        assert only(unstated, "F3.3.hsa").status is Status.INSUFFICIENT_DATA
        none = tax_advantaged.check(
            snap(profile=Profile(employer_plan_stated=True, hsa_stated=True))
        )
        assert only(none, "F3.3.match").status is Status.NOT_APPLICABLE
        assert only(none, "F3.3.hsa").status is Status.NOT_APPLICABLE

    def test_missing_limits_file_is_insufficient(self):
        rules = dataclasses.replace(
            RULES, limits=None, limits_error="No contribution limits on file for 2026"
        )
        o = only(tax_advantaged.check(snap(profile=worker(), rules=rules)))
        assert o.key == "F3.3.limits" and o.status is Status.INSUFFICIENT_DATA


# --------------------------------------------------------------------------
TARGET = {"us_equity": Decimal("0.60"), "intl_equity": Decimal("0.30"), "us_bond": Decimal("0.10")}


class TestAllocation:
    def test_drift_beyond_tolerance(self):
        """55,000 / 20,000 / 25,000 against 60 / 30 / 10: bonds +15 pts (> 2 × 5) → MEDIUM."""
        o = only(
            allocation.check(
                snap(
                    acct(
                        "Brokerage",
                        "brokerage",
                        positions=[("VTI", "55000"), ("VXUS", "20000"), ("BND", "25000")],
                    ),
                    profile=Profile(target_allocation=TARGET, drift_tolerance=Decimal("0.05")),
                )
            )
        )
        assert o.status is Status.ATTENTION and o.severity is Severity.MEDIUM
        assert "US bonds" in o.summary and "+15.0 pts" in o.summary and "$15,000.00" in o.summary

    def test_target_date_fund_is_looked_through(self):
        """VTTSX 54/36/7/3 against 54/36/10: bonds −3, intl bonds +3 → within ±5."""
        target = {
            "us_equity": Decimal("0.54"),
            "intl_equity": Decimal("0.36"),
            "us_bond": Decimal("0.10"),
        }
        o = only(
            allocation.check(
                snap(
                    acct("401k", "retirement_401k", positions=[("VTTSX", "100000")]),
                    profile=Profile(target_allocation=target, drift_tolerance=Decimal("0.05")),
                )
            )
        )
        assert o.status is Status.OK

    def test_mostly_unclassified_portfolio_is_insufficient(self):
        """40,000 classified; 60,000 401(k) balance without holdings → 60% > 50%."""
        o = only(
            allocation.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("VTI", "40000")]),
                    acct("Work 401k", "retirement_401k", "60000"),
                    profile=Profile(target_allocation=TARGET),
                )
            )
        )
        assert o.status is Status.INSUFFICIENT_DATA
        assert any("Work 401k" in m for m in o.missing)

    def test_no_target_still_shows_the_allocation(self):
        o = only(
            allocation.check(snap(acct("Brokerage", "brokerage", positions=[("VTI", "100000")])))
        )
        assert o.status is Status.INSUFFICIENT_DATA
        assert fact(o, "US stocks") == "100.0% ($100,000.00)"

    def test_no_investment_accounts(self):
        assert (
            only(allocation.check(snap(acct("Checking", "checking", "1")))).status
            is Status.NOT_APPLICABLE
        )


# --------------------------------------------------------------------------
class TestLocation:
    def test_bonds_in_taxable_while_stocks_sit_in_401k(self):
        o = only(
            location.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("BND", "50000")]),
                    acct("Work 401k", "retirement_401k", positions=[("VTI", "80000")]),
                )
            )
        )
        assert o.status is Status.ATTENTION and o.professional_review
        assert "Up to $50,000.00" in o.summary

    def test_401k_recorded_as_taxable_is_inferred(self):
        o = only(
            location.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("BND", "50000")]),
                    acct(
                        "Work 401k", "retirement_401k", positions=[("VTI", "80000")], tax="taxable"
                    ),
                )
            )
        )
        assert o.status is Status.ATTENTION
        assert any("inferred" in a for a in o.assumptions)

    def test_missing_401k_holdings_is_insufficient_not_fine(self):
        o = only(
            location.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("BND", "50000")]),
                    acct("Work 401k", "retirement_401k", "80000"),
                )
            )
        )
        assert o.status is Status.INSUFFICIENT_DATA

    def test_taxable_only_is_not_applicable(self):
        o = only(location.check(snap(acct("Brokerage", "brokerage", positions=[("BND", "50000")]))))
        assert o.status is Status.NOT_APPLICABLE

    def test_stocks_already_in_taxable(self):
        o = only(
            location.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("VTI", "50000")]),
                    acct("Work 401k", "retirement_401k", positions=[("BND", "80000")]),
                )
            )
        )
        assert o.status is Status.OK


# --------------------------------------------------------------------------
PRICEY = security("PRICEY", "0.0085", diversified=True, category="us_total_market")


class TestFees:
    def test_cheaper_same_category_fund(self):
        """100,000 × 0.85% = 850/yr; VTI at 0.03% saves 100,000 × 0.82% = 820 → MEDIUM."""
        o = only(
            fees.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("PRICEY", "100000")]),
                    rules=rules_with(PRICEY),
                )
            )
        )
        assert o.severity is Severity.MEDIUM and o.annual_impact == Money("820.00")
        assert fact(o, "Annual fund costs") == "$850.00/yr"
        assert any("VTI" in d for d in o.detail)

    def test_no_alternative_suggested_inside_a_401k_menu(self):
        o = only(
            fees.check(
                snap(
                    acct("Work 401k", "retirement_401k", positions=[("PRICEY", "100000")]),
                    rules=rules_with(PRICEY),
                )
            )
        )
        assert o.status is Status.ATTENTION and o.annual_impact is None
        assert any("menu" in d for d in o.detail)

    def test_low_cost_portfolio(self):
        """100,000 × 0.03% = 30."""
        o = only(fees.check(snap(acct("Brokerage", "brokerage", positions=[("VTI", "100000")]))))
        assert o.status is Status.OK and "$30.00" in o.summary

    def test_unknown_funds_only(self):
        o = only(fees.check(snap(acct("Brokerage", "brokerage", positions=[("MYST", "1000")]))))
        assert o.status is Status.INSUFFICIENT_DATA


# --------------------------------------------------------------------------
ACME = security("ACME")


class TestConcentration:
    @pytest.mark.parametrize(
        "acme, severity", [("15000", Severity.MEDIUM), ("25000", Severity.HIGH)]
    )
    def test_employer_stock(self, acme, severity):
        """15% (> 10%, ≤ 20%) → MEDIUM; 25% (> 20%) → HIGH."""
        rest = str(100000 - int(acme))
        o = only(
            concentration.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("VTI", rest), ("ACME", acme)]),
                    profile=worker(stock="ACME"),
                    rules=rules_with(ACME),
                )
            )
        )
        assert o.severity is severity and "employer" in o.summary

    @pytest.mark.parametrize("xyz, severity", [("12000", Severity.LOW), ("25000", Severity.MEDIUM)])
    def test_single_stock(self, xyz, severity):
        rest = str(100000 - int(xyz))
        o = only(
            concentration.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("VTI", rest), ("XYZ", xyz)]),
                    rules=rules_with(security("XYZ")),
                )
            )
        )
        assert o.severity is severity

    def test_unmarked_large_holding_is_insufficient(self):
        o = only(
            concentration.check(
                snap(
                    acct("Brokerage", "brokerage", positions=[("VTI", "70000"), ("MYST", "30000")]),
                )
            )
        )
        assert o.status is Status.INSUFFICIENT_DATA
        assert any("securities.local.yml" in m for m in o.missing)

    def test_no_holdings_is_insufficient_not_fine(self):
        o = only(concentration.check(snap(acct("Work 401k", "retirement_401k", "100000"))))
        assert o.status is Status.INSUFFICIENT_DATA

    def test_diversified_fund_is_never_flagged(self):
        o = only(
            concentration.check(snap(acct("Brokerage", "brokerage", positions=[("VTI", "100000")])))
        )
        assert o.status is Status.OK


# --------------------------------------------------------------------------
class TestDebt:
    def test_high_rate_debt_and_ordering(self):
        """Car 12,000 × 8.9% = 1,068 (≥ 8% rate; ≥ 1,000/yr → HIGH). Card paid in full: excluded."""
        o = only(
            debt.check(
                snap(
                    acct("Car Loan", "auto_loan", "-12000", rate="0.089", kind="fixed"),
                    acct("Mortgage", "mortgage", "-310000", rate="0.0625", kind="fixed"),
                    acct("Card", "credit_card", "-1400", rate="0.2499", revolving=False),
                )
            )
        )
        assert o.severity is Severity.HIGH and o.annual_impact == Money("1068.00")
        assert fact(o, "Estimated interest") == "$20,443.00/yr"  # 1,068 + 19,375
        assert o.detail[1].startswith("1. Car Loan")
        assert any("paid in full" in a and "Card" in a for a in o.assumptions)

    def test_card_with_unknown_revolving_status_is_not_assumed(self):
        o = only(debt.check(snap(acct("Card", "credit_card", "-1400", rate="0.2499"))))
        assert o.status is Status.INSUFFICIENT_DATA
        assert "--revolving" in o.missing[0]

    def test_promotional_rate_ending(self):
        """3,000 at 0% until 30 days out, then 27.99%: 3,000 × 27.99% = 839.70/yr."""
        o = only(
            debt.check(
                snap(
                    acct(
                        "Card",
                        "credit_card",
                        "-3000",
                        rate="0",
                        revolving=True,
                        promo_ends=AS_OF + timedelta(days=30),
                        post_promo="0.2799",
                    )
                )
            )
        )
        assert o.severity is Severity.MEDIUM
        assert any("$839.70" in d for d in o.detail)

    def test_orderings_differ(self):
        o = only(
            debt.check(
                snap(
                    acct("Big", "auto_loan", "-5000", rate="0.20"),
                    acct("Small", "auto_loan", "-1000", rate="0.10"),
                )
            )
        )
        assert any(d.startswith("Smallest balance first") and "Small, Big" in d for d in o.detail)

    def test_variable_rate_only(self):
        o = only(
            debt.check(snap(acct("Mortgage", "mortgage", "-200000", rate="0.05", kind="variable")))
        )
        assert o.severity is Severity.LOW

    def test_no_debts(self):
        assert (
            only(debt.check(snap(acct("Checking", "checking", "1")))).status
            is Status.NOT_APPLICABLE
        )


# --------------------------------------------------------------------------
class TestInsurance:
    def test_life_and_disability_gaps(self):
        profile = Profile(
            dependents=2,
            annual_salary=Money("96000"),
            insurance_stated=True,
            life_coverage=Money("250000"),
            long_term_disability=False,
            umbrella_coverage=Money(0),
        )
        o = only(insurance.check(snap(profile=profile)))
        assert o.severity is Severity.MEDIUM and o.professional_review
        assert len(o.detail) == 2
        assert any("$960,000.00" in d for d in o.detail)  # 10 × 96,000

    def test_umbrella_below_net_worth(self):
        profile = Profile(
            dependents=0,
            annual_salary=Money("100000"),
            insurance_stated=True,
            life_coverage=Money(0),
            long_term_disability=True,
            umbrella_coverage=Money(0),
        )
        o = only(insurance.check(snap(acct("Brokerage", "brokerage", "600000"), profile=profile)))
        assert o.severity is Severity.LOW and "$600,000.00" in o.summary

    def test_not_stated(self):
        o = only(insurance.check(snap(profile=Profile())))
        assert o.status is Status.INSUFFICIENT_DATA and o.professional_review


# --------------------------------------------------------------------------
class TestSavings:
    def test_savings_rate(self):
        o = only(savings.check(snap(spending="3000", income="5000")))
        assert o.status is Status.OK and fact(o, "Savings rate") == "40.0%"

    def test_workplace_contributions_are_added_to_income(self):
        """120,000 × 5% / 12 = 500/mo. Income 5,500, saved 2,500 → 45.5%."""
        o = only(
            savings.check(
                snap(
                    spending="3000",
                    income="5000",
                    profile=worker(salary="120000", contribution="0.05"),
                )
            )
        )
        assert fact(o, "Workplace retirement contributions") == "$500.00/mo"
        assert fact(o, "Savings rate") == "45.5%"

    def test_spending_above_income(self):
        o = only(savings.check(snap(spending="5200", income="5000")))
        assert o.severity is Severity.HIGH and "$200.00" in o.summary

    def test_low_savings_rate(self):
        o = only(savings.check(snap(spending="4800", income="5000")))
        assert o.severity is Severity.LOW  # 4% < 10%

    def test_short_history_is_insufficient(self):
        o = only(savings.check(snap(spending="3000", income="5000", days=30)))
        assert o.status is Status.INSUFFICIENT_DATA


# --------------------------------------------------------------------------
class TestEngineAndModel:
    def test_a_crashing_check_does_not_hide_the_others(self, monkeypatch):
        def boom(_snapshot):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            engine,
            "CHECKS",
            (("F3.1", "Emergency fund", boom), ("F3.10", "Savings rate", savings.check)),
        )
        results = engine.run_checks(snap(spending="3000", income="5000"))
        assert [o.status for o in results] == [Status.ERROR, Status.OK]
        assert "kaboom" in results[0].summary

    def test_unknown_check_id_is_refused(self):
        with pytest.raises(ValueError, match="F9.9"):
            engine.run_checks(snap(), ["F9.9"])

    def test_all_checks_run_on_an_empty_snapshot_without_errors(self):
        results = engine.run_checks(snap(benchmark=None))
        assert not [o for o in results if o.status is Status.ERROR]

    @pytest.mark.parametrize(
        "fields, message",
        [
            (dict(status=Status.ATTENTION), "requires a severity"),
            (dict(status=Status.OK, severity=Severity.LOW), "only applies"),
            (dict(status=Status.INSUFFICIENT_DATA), "must say what is missing"),
            (dict(status=Status.OK, annual_impact=Money("-1")), "magnitude"),
        ],
    )
    def test_observation_invariants(self, fields, message):
        with pytest.raises(ValueError, match=message):
            Observation(key="F3.1.x", check_id="F3.1", title="t", summary="s", **fields)

    def test_report_groups_and_flags_professional_review(self):
        results = [
            Observation(
                "F3.9.coverage",
                "F3.9",
                "Insurance",
                Status.ATTENTION,
                "gap",
                severity=Severity.MEDIUM,
                professional_review=True,
            ),
            Observation("F3.1.coverage", "F3.1", "Emergency fund", Status.OK, "fine"),
        ]
        text = format_observations(results, as_of=AS_OF)
        assert text.index("NEEDS ATTENTION (1)") < text.index("LOOKS FINE (1)")
        assert "licensed professional" in text
