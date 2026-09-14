"""Holdings (positions) import."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_advisor.db.store import (
    create_account,
    find_account,
    latest_balances,
    latest_positions,
)
from financial_advisor.importers.csv_import import ImportError_
from financial_advisor.importers.holdings_import import import_holdings, parse_holdings
from financial_advisor.money import Money, NonUSDCurrencyError

from .conftest import FIXTURES

AS_OF = date(2026, 9, 13)
BROKERAGE_EXPORT = FIXTURES / "holdings_brokerage.csv"


def test_real_world_shape_parses():
    """The export has a split tax lot, a sweep fund, pending activity, and a footer."""
    holdings, skipped = parse_holdings(BROKERAGE_EXPORT.read_text())
    assert [h.symbol for h in holdings] == ["VTI", "BND", "VTI", "SPAXX"]
    assert holdings[0].market_value == Money("60000.00")
    assert holdings[3].quantity is None


def test_rows_with_money_that_are_not_holdings_are_listed_not_dropped():
    _, skipped = parse_holdings(BROKERAGE_EXPORT.read_text())
    assert len(skipped) == 1
    assert "Pending Activity" in skipped[0] and "-$100.00" in skipped[0]


def test_footer_text_is_not_mistaken_for_another_account():
    # Footer text sits in the first column, which is the account column.
    parse_holdings(BROKERAGE_EXPORT.read_text())  # must not raise "2 accounts"


def test_multiple_accounts_require_a_choice():
    text = (
        "Account Number,Symbol,Quantity,Current Value\n"
        'Z00000001,VTI,10,"$3,000.00"\n'
        'Z00000002,BND,10,"$750.00"\n'
    )
    with pytest.raises(ImportError_, match="2 accounts"):
        parse_holdings(text)
    holdings, _ = parse_holdings(text, only="Z00000002")
    assert [h.symbol for h in holdings] == ["BND"]


def test_the_error_does_not_echo_account_numbers():
    text = "Account Number,Symbol,Current Value\nZ00000001,VTI,1\nZ00000002,BND,1\n"
    with pytest.raises(ImportError_) as exc:
        parse_holdings(text)
    assert "Z00000001" not in str(exc.value)


def test_non_usd_is_refused():
    text = "Symbol,Current Value,Currency\nVTI,100,EUR\n"
    with pytest.raises(NonUSDCurrencyError):
        parse_holdings(text)


def test_a_transaction_export_is_explained():
    with pytest.raises(ImportError_, match="holdings"):
        parse_holdings("Date,Description,Amount\n2026-01-01,COFFEE,-4.50\n")


@pytest.fixture()
def brokerage(db):
    create_account(db, name="Brokerage", type_code="brokerage")
    return find_account(db, "Brokerage")


def test_import_combines_lots_sets_balance_and_is_idempotent(db, brokerage):
    """VTI 60,000 + 3,000 = 63,000 over 210 shares; BND 30,000; SPAXX 2,500 → 95,500."""
    result = import_holdings(
        db, BROKERAGE_EXPORT, account_id=brokerage.id, account_name="Brokerage", as_of=AS_OF
    )
    assert result.positions_written == 3
    assert result.total == Money("95500.00")

    positions = {p.symbol: p for p in latest_positions(db, AS_OF)[brokerage.id]}
    assert positions["VTI"].market_value == Money("63000.00")
    assert positions["VTI"].quantity == Decimal("210")
    assert latest_balances(db, AS_OF)[brokerage.id]["amount_cents"] == 9550000

    again = import_holdings(
        db, BROKERAGE_EXPORT, account_id=brokerage.id, account_name="Brokerage", as_of=AS_OF
    )
    assert again.skipped_file


def test_a_new_export_replaces_the_day_rather_than_merging(db, brokerage, tmp_path):
    first = tmp_path / "first.csv"
    first.write_text("Symbol,Current Value\nVTI,1000\nBND,500\n")
    second = tmp_path / "second.csv"
    second.write_text("Symbol,Current Value\nVTI,1100\n")
    for path in (first, second):
        import_holdings(db, path, account_id=brokerage.id, account_name="Brokerage", as_of=AS_OF)

    symbols = [p.symbol for p in latest_positions(db, AS_OF)[brokerage.id]]
    assert symbols == ["VTI"]  # BND was sold; it must not linger
