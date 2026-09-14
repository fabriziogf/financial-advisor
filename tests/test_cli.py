"""The `fa` command line, end to end against an isolated data directory."""

from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from financial_advisor.cli import cli
from financial_advisor.db.connection import _SCHEMA_FILE, connect
from financial_advisor.paths import data_dir, database_path

from .conftest import FIXTURES


@pytest.fixture()
def fa():
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(cli, [str(a) for a in args])

    return invoke


@pytest.fixture()
def initialized(fa):
    assert fa("init").exit_code == 0
    return fa


def test_check_runs_and_warns_about_an_unmodified_example_profile(initialized):
    fa = initialized
    assert fa("profile", "init").exit_code == 0
    result = fa("check", "--as-of", "2026-09-13")
    assert result.exit_code == 0
    assert "unmodified example" in result.output
    assert "Financial check-up as of 2026-09-13" in result.output


def test_profile_init_refuses_to_overwrite(initialized):
    initialized("profile", "init")
    result = initialized("profile", "init")
    assert result.exit_code == 1 and "--force" in result.output


def test_terms_takes_percent_and_catches_unit_mistakes(initialized):
    fa = initialized
    fa("account-add", "--name", "Savings", "--type", "savings")
    bad = fa("terms", "--account", "Savings", "--rate", "425")
    assert bad.exit_code == 1 and "between 0 and 100" in bad.output
    good = fa("terms", "--account", "Savings", "--rate", "4.25")
    assert good.exit_code == 0 and "4.25%" in good.output


def test_unknown_check_id(initialized):
    result = initialized("check", "--only", "F9.9")
    assert result.exit_code == 1 and "F3.10" in result.output


def test_a_malformed_profile_is_reported_cleanly(initialized):
    data_dir().mkdir(parents=True, exist_ok=True)
    (data_dir() / "profile.yml").write_text("emergency_funds:\n  target_months: 6\n")
    result = initialized("check")
    assert result.exit_code == 1
    assert "unknown section" in result.output and "Traceback" not in result.output


def test_account_add_infers_tax_treatment_from_type(initialized):
    initialized("account-add", "--name", "Work 401k", "--type", "retirement_401k")
    with connect() as conn:
        row = conn.execute("SELECT tax_treatment FROM account WHERE name = 'Work 401k'").fetchone()
    assert row["tax_treatment"] == "tax_deferred"


def test_holdings_import_through_the_cli(initialized):
    fa = initialized
    fa("account-add", "--name", "Brokerage", "--type", "brokerage")
    result = fa(
        "holdings",
        FIXTURES / "holdings_brokerage.csv",
        "--account",
        "Brokerage",
        "--as-of",
        "2026-09-13",
    )
    assert result.exit_code == 0, result.output
    assert "3 positions" in result.output and "$95,500.00" in result.output
    assert "Pending Activity" in result.output
    assert "SPAXX" in result.output  # not in the catalog, and the output says so


def test_holdings_refused_for_a_non_investment_account(initialized):
    initialized("account-add", "--name", "Checking", "--type", "checking")
    result = initialized("holdings", FIXTURES / "holdings_brokerage.csv", "--account", "Checking")
    assert result.exit_code == 1 and "investment" in result.output


def test_an_outdated_database_is_refused_then_upgraded_by_init(fa):
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path())
    conn.executescript(_SCHEMA_FILE.read_text())
    conn.close()

    refused = fa("accounts")
    assert refused.exit_code == 1 and "fa init" in refused.output

    upgraded = fa("init")
    assert upgraded.exit_code == 0 and "Upgraded schema v1 → v2" in upgraded.output
    assert "Backup" in upgraded.output
    assert fa("accounts").exit_code == 0
