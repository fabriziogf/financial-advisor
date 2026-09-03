"""Net worth tests.

Expected totals are computed by hand in the comments (R1). The point of a
hand-computed fixture is that it fails when the code and the arithmetic disagree —
a total derived from the same code it is testing proves nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

from financial_advisor.db.store import create_account, find_account, set_balance
from financial_advisor.money import Money
from financial_advisor.reports.net_worth import (
    STALE_AFTER_DAYS,
    compute_net_worth,
    format_net_worth,
)

TODAY = date(2026, 6, 15)


def _account(conn, name, type_code, **kw):
    create_account(conn, name=name, type_code=type_code, **kw)
    return find_account(conn, name)


def _populate(conn, *, as_of=TODAY):
    """A deliberately mixed portfolio, including inconsistent liability signs.

      Assets:      5,000.00  checking
                  42,350.75  brokerage
                 450,000.00  house (declared)
                 -----------
                 497,350.75

      Liabilities: 285,000.00  mortgage   (stored NEGATIVE by the institution)
                     1,200.50  card       (stored POSITIVE by the institution)
                 -----------
                 286,200.50

      Net worth:   211,150.25
    """
    checking = _account(conn, "Checking", "checking")
    brokerage = _account(conn, "Brokerage", "brokerage")
    house = _account(conn, "House", "real_estate", is_manual=True)
    mortgage = _account(conn, "Mortgage", "mortgage")
    card = _account(conn, "Card", "credit_card")

    set_balance(conn, checking.id, Money("5000.00"), as_of)
    set_balance(conn, brokerage.id, Money("42350.75"), as_of)
    set_balance(conn, house.id, Money("450000.00"), as_of)
    set_balance(conn, mortgage.id, Money("-285000.00"), as_of)
    set_balance(conn, card.id, Money("1200.50"), as_of)


class TestTotals:
    def test_total_assets(self, db):
        _populate(db)
        assert compute_net_worth(db, TODAY).total_assets == Money("497350.75")

    def test_total_liabilities(self, db):
        _populate(db)
        assert compute_net_worth(db, TODAY).total_liabilities == Money("286200.50")

    def test_net_worth(self, db):
        _populate(db)
        assert compute_net_worth(db, TODAY).net_worth == Money("211150.25")

    def test_exact_to_the_cent(self, db):
        # The float-error canary: 0.75 + 0.50 must not drift.
        _populate(db)
        assert compute_net_worth(db, TODAY).net_worth.cents == 21115025


class TestLiabilitySigns:
    """Institutions disagree on how to sign a debt. The taxonomy decides, not the file."""

    def test_negative_stored_liability_still_subtracts(self, db):
        _populate(db)
        report = compute_net_worth(db, TODAY)
        mortgage = next(b for b in report.liabilities if b.account.name == "Mortgage")
        assert mortgage.magnitude == Money("285000.00")

    def test_positive_stored_liability_still_subtracts(self, db):
        _populate(db)
        report = compute_net_worth(db, TODAY)
        card = next(b for b in report.liabilities if b.account.name == "Card")
        assert card.magnitude == Money("1200.50")

    def test_liabilities_never_appear_as_assets(self, db):
        _populate(db)
        report = compute_net_worth(db, TODAY)
        assert {b.account.name for b in report.assets} == {"Checking", "Brokerage", "House"}

    def test_sign_flip_would_change_the_answer(self, db):
        # Guards the guard: if liabilities were added instead of subtracted the
        # total would be 783,551.25, so this test genuinely discriminates.
        _populate(db)
        report = compute_net_worth(db, TODAY)
        assert report.net_worth != report.total_assets + report.total_liabilities


class TestStaleness:
    """R5 — say when you don't know."""

    def test_fresh_balances_are_not_stale(self, db):
        _populate(db)
        assert compute_net_worth(db, TODAY).stale == []

    def test_old_balance_is_flagged(self, db):
        _populate(db)
        old = _account(db, "Forgotten Savings", "savings")
        set_balance(db, old.id, Money("1000.00"), TODAY - timedelta(days=200))
        report = compute_net_worth(db, TODAY)
        assert [b.account.name for b in report.stale] == ["Forgotten Savings"]

    def test_stale_balance_is_still_counted(self, db):
        # Flagged, not excluded — dropping it would understate net worth silently.
        _populate(db)
        old = _account(db, "Forgotten Savings", "savings")
        set_balance(db, old.id, Money("1000.00"), TODAY - timedelta(days=200))
        assert compute_net_worth(db, TODAY).total_assets == Money("498350.75")

    def test_boundary_is_not_stale(self, db):
        _populate(db)
        edge = _account(db, "Edge", "savings")
        set_balance(db, edge.id, Money("10.00"), TODAY - timedelta(days=STALE_AFTER_DAYS))
        assert compute_net_worth(db, TODAY).stale == []

    def test_report_is_marked_incomplete_when_stale(self, db):
        _populate(db)
        old = _account(db, "Forgotten Savings", "savings")
        set_balance(db, old.id, Money("1000.00"), TODAY - timedelta(days=200))
        assert compute_net_worth(db, TODAY).is_complete is False


class TestMissingData:
    def test_account_without_balance_is_listed_as_missing(self, db):
        _populate(db)
        _account(db, "New Account", "savings")
        report = compute_net_worth(db, TODAY)
        assert [b.account.name for b in report.missing] == ["New Account"]

    def test_missing_account_does_not_silently_count_as_zero(self, db):
        _populate(db)
        _account(db, "New Account", "savings")
        report = compute_net_worth(db, TODAY)
        assert report.is_complete is False
        assert report.total_assets == Money("497350.75")


class TestAsOf:
    def test_future_balances_are_excluded(self, db):
        _populate(db, as_of=TODAY)
        checking = find_account(db, "Checking")
        set_balance(db, checking.id, Money("9999.00"), TODAY + timedelta(days=10))
        assert compute_net_worth(db, TODAY).total_assets == Money("497350.75")

    def test_most_recent_balance_at_or_before_date_wins(self, db):
        _populate(db, as_of=TODAY - timedelta(days=10))
        checking = find_account(db, "Checking")
        set_balance(db, checking.id, Money("6000.00"), TODAY - timedelta(days=2))
        assert compute_net_worth(db, TODAY).total_assets == Money("498350.75")


class TestFormatting:
    def test_totals_appear(self, db):
        _populate(db)
        out = format_net_worth(compute_net_worth(db, TODAY))
        assert "$211,150.25" in out
        assert "NET WORTH" in out

    def test_stale_warning_is_surfaced(self, db):
        _populate(db)
        old = _account(db, "Forgotten Savings", "savings")
        set_balance(db, old.id, Money("1000.00"), TODAY - timedelta(days=200))
        out = format_net_worth(compute_net_worth(db, TODAY))
        assert "only as current as its oldest input" in out

    def test_missing_accounts_are_surfaced(self, db):
        _populate(db)
        _account(db, "New Account", "savings")
        out = format_net_worth(compute_net_worth(db, TODAY))
        assert "EXCLUDED" in out
        assert "incomplete" in out
