"""Profile loading and validation (F1.7)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_advisor.money import Money
from financial_advisor.profile import (
    ProfileError,
    example_profile_path,
    load_profile,
    parse_profile,
)
from financial_advisor.rules import load_asset_classes
from financial_advisor.yamlio import load_yaml_text

CLASSES = set(load_asset_classes())
TODAY = date(2026, 9, 13)


def parse(text: str):
    return parse_profile(load_yaml_text(text), asset_classes=CLASSES, today=TODAY)


def test_example_profile_is_valid():
    profile = load_profile(example_profile_path(), asset_classes=CLASSES, today=TODAY)
    assert profile is not None
    assert profile.annual_salary == Money("100000.00")


def test_numbers_are_exact_decimals_never_floats():
    profile = load_profile(example_profile_path(), asset_classes=CLASSES, today=TODAY)
    assert all(isinstance(v, Decimal) for v in profile.target_allocation.values())
    assert isinstance(profile.employer_plan.contribution_rate, Decimal)
    assert profile.target_allocation["us_bond"] == Decimal("0.10")


def test_missing_file_is_none_not_an_error(tmp_path):
    assert load_profile(tmp_path / "nope.yml", asset_classes=CLASSES, today=TODAY) is None


class TestMatchFormula:
    """100% of the first 3%, then 50% of the next 2% (cumulative bound 5%).

    maximum   = 1.00 × 3% + 0.50 × 2%  = 4.0%
    at 2%     = 1.00 × 2%              = 2.0%
    at 4%     = 1.00 × 3% + 0.50 × 1%  = 3.5%
    at 10%    = capped                 = 4.0%
    """

    plan = parse(
        "employer_plan:\n  contribution_percent: 6\n  match:\n"
        "    - {rate_percent: 100, up_to_percent: 3}\n"
        "    - {rate_percent: 50, up_to_percent: 5}\n"
    ).employer_plan

    def test_maximum(self):
        assert self.plan.max_match_rate() == Decimal("0.04")

    @pytest.mark.parametrize(
        "contribution, expected",
        [("0.02", "0.02"), ("0.04", "0.035"), ("0.10", "0.04"), ("0", "0")],
    )
    def test_at_contribution(self, contribution, expected):
        assert self.plan.match_rate_at(Decimal(contribution)) == Decimal(expected)

    def test_full_match_contribution(self):
        assert self.plan.full_match_contribution == Decimal("0.05")


class TestStatedVersusNone:
    def test_absent_section_is_not_stated(self):
        profile = parse("income:\n  annual_salary: 1\n")
        assert not profile.employer_plan_stated
        assert not profile.hsa_stated

    def test_null_section_is_an_explicit_none(self):
        profile = parse("employer_plan: null\nhsa: null\n")
        assert profile.employer_plan_stated and profile.employer_plan is None
        assert profile.hsa_stated and profile.hsa is None


class TestValidation:
    def test_every_problem_is_reported_at_once(self):
        with pytest.raises(ProfileError) as exc:
            parse("emergency_funds:\n  target_months: 6\nincome:\n  salary: 5\n")
        assert len(exc.value.problems) == 2
        assert any("emergency_funds" in p for p in exc.value.problems)
        assert any("income.salary" in p for p in exc.value.problems)

    def test_match_bounds_must_be_cumulative(self):
        with pytest.raises(ProfileError, match="cumulative"):
            parse(
                "employer_plan:\n  match:\n"
                "    - {rate_percent: 100, up_to_percent: 3}\n"
                "    - {rate_percent: 50, up_to_percent: 2}\n"
            )

    def test_target_allocation_must_sum_to_100(self):
        with pytest.raises(ProfileError, match="sum to 100"):
            parse("investments:\n  target_allocation_percent: {us_equity: 60, us_bond: 30}\n")

    def test_unknown_asset_class_is_rejected(self):
        with pytest.raises(ProfileError, match="unknown asset class"):
            parse("investments:\n  target_allocation_percent: {us_equities: 100}\n")

    def test_negative_money_is_rejected(self):
        with pytest.raises(ProfileError, match="negative"):
            parse("ira:\n  contributed_this_year: -5\n")

    def test_hsa_section_requires_coverage(self):
        with pytest.raises(ProfileError, match="hsa.coverage"):
            parse("hsa:\n  contributed_this_year: 100\n")

    def test_percent_out_of_range(self):
        with pytest.raises(ProfileError, match="between 0 and 100"):
            parse("employer_plan:\n  contribution_percent: 150\n")


def test_age_is_the_age_attained_by_year_end():
    assert parse("household:\n  birth_year: 1976\n").age_in(2026) == 50
