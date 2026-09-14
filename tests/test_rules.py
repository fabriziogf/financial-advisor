"""Rules files: contribution limits, thresholds, securities catalog (R3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from financial_advisor.money import Money
from financial_advisor.rules import (
    LimitsNotFoundError,
    RulesError,
    load_asset_classes,
    load_limits,
    load_securities,
    load_thresholds,
)
from financial_advisor.yamlio import load_yaml_text

CLASSES = load_asset_classes()


class TestLimits2026:
    """Figures from IRS Notice 2025-67 and Rev. Proc. 2025-19."""

    limits = load_limits(2026)

    @pytest.mark.parametrize(
        "age, expected",
        [
            (49, "24500.00"),  # base
            (50, "32500.00"),  # + 8,000
            (59, "32500.00"),
            (60, "35750.00"),  # + 11,250 instead
            (63, "35750.00"),
            (64, "32500.00"),  # back to + 8,000 — not "60 and over"
        ],
    )
    def test_deferral_limit_by_age(self, age, expected):
        assert self.limits.deferral_limit(age) == Money(expected)

    @pytest.mark.parametrize("age, expected", [(49, "7500.00"), (50, "8600.00")])
    def test_ira_limit(self, age, expected):
        assert self.limits.ira_limit(age) == Money(expected)

    @pytest.mark.parametrize(
        "coverage, age, expected",
        [
            ("self", 54, "4400.00"),
            ("self", 55, "5400.00"),
            ("family", 40, "8750.00"),
            ("family", 55, "9750.00"),
        ],
    )
    def test_hsa_limit(self, coverage, age, expected):
        assert self.limits.hsa_limit(coverage, age) == Money(expected)

    def test_compensation_and_roth_thresholds(self):
        assert self.limits.compensation_limit == Money("360000.00")
        assert self.limits.roth_catch_up_wage_threshold == Money("150000.00")


def test_a_missing_year_is_refused_rather_than_borrowed():
    with pytest.raises(LimitsNotFoundError, match="R3"):
        load_limits(2099)


def test_thresholds_are_exact_decimals():
    value = load_thresholds().decimal("fees", "high_expense_ratio")
    assert isinstance(value, Decimal) and value == Decimal("0.0020")


def test_a_missing_threshold_is_an_error_not_a_default():
    with pytest.raises(RulesError, match="fees.nope"):
        load_thresholds().decimal("fees", "nope")


class TestSecurities:
    def test_tracked_catalog_loads_with_look_through(self):
        catalog = load_securities(CLASSES, local_path=None)
        assert sum(catalog["VTTSX"].weights.values()) == 1
        assert catalog["VTI"].diversified is True

    def overlay(self, tmp_path, text):
        path = tmp_path / "securities.local.yml"
        path.write_text(text)
        return load_securities(CLASSES, local_path=path)

    def test_local_overlay_replaces_a_tracked_entry(self, tmp_path):
        catalog = self.overlay(
            tmp_path, "VTI:\n  expense_ratio: '0.0004'\n  asset_class: {us_equity: 1}\n"
        )
        assert catalog["VTI"].source == "local"
        assert catalog["VTI"].expense_ratio == Decimal("0.0004")

    def test_yaml_boolean_ticker_is_rejected_with_a_fix(self, tmp_path):
        with pytest.raises(RulesError, match="Quote"):
            self.overlay(tmp_path, "ON:\n  name: ON Semiconductor\n")

    def test_holdings_fields_are_rejected(self, tmp_path):
        with pytest.raises(RulesError, match="Holdings data"):
            self.overlay(tmp_path, "XYZ:\n  shares: 10\n")

    def test_near_one_weights_are_normalized(self, tmp_path):
        catalog = self.overlay(tmp_path, "MIX:\n  asset_class: {us_equity: 0.5, us_bond: 0.495}\n")
        assert sum(catalog["MIX"].weights.values()) == 1

    def test_weights_far_from_one_are_rejected(self, tmp_path):
        with pytest.raises(RulesError, match="sum to"):
            self.overlay(tmp_path, "MIX:\n  asset_class: {us_equity: 0.5, us_bond: 0.3}\n")

    def test_unknown_asset_class_is_rejected(self, tmp_path):
        with pytest.raises(RulesError, match="unknown asset class"):
            self.overlay(tmp_path, "XYZ:\n  asset_class: {crypto_moonshots: 1}\n")

    def test_expense_ratio_written_as_percent_is_caught(self, tmp_path):
        with pytest.raises(RulesError, match="fraction"):
            self.overlay(tmp_path, "XYZ:\n  expense_ratio: 0.85\n")


class TestYaml:
    def test_floats_become_exact_decimals(self):
        assert load_yaml_text("x: 0.1") == {"x": Decimal("0.1")}

    def test_non_finite_numbers_are_refused(self):
        with pytest.raises(Exception, match="non-finite|exact number"):
            load_yaml_text("x: .inf")
