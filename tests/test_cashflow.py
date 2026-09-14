"""Spending and income derived from transactions (F3.1, F3.10).

Expected figures are worked out by hand (R1). The months-per-day factor is
30.4375 / days, and each monthly figure is rounded to the cent once.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import count

from financial_advisor.analysis.cashflow import (
    TxnRecord,
    classify,
    match_transfers,
    summarize_cashflow,
)
from financial_advisor.money import Money

AS_OF = date(2026, 9, 13)
_ids = count(1)


def txn(account_id: int, account_type: str, day: date, amount: str, description: str) -> TxnRecord:
    return TxnRecord(next(_ids), account_id, account_type, day, Money(amount), description)


def summarize(rows):
    return summarize_cashflow(rows, as_of=AS_OF, window_days=4, max_history_days=365)


def realistic_household() -> list[TxnRecord]:
    """120 fictional days, 17 May – 13 Sep 2026. Accounts: 1 checking, 2 savings, 3 card."""
    start = AS_OF - timedelta(days=119)
    rows: list[TxnRecord] = []
    d = start
    while d <= AS_OF:
        if d.day in (1, 15):
            rows.append(txn(1, "checking", d, "3100.00", "PAYROLL TEST EMPLOYER DIR DEP"))
        if d.day == 1:
            rows.append(txn(1, "checking", d, "-1900.00", "RENT PROPERTY MGMT"))
        if d.day == 5:
            rows.append(txn(1, "checking", d, "-500.00", "ONLINE TRANSFER TO SAVINGS"))
            rows.append(txn(2, "savings", d, "500.00", "ONLINE TRANSFER FROM CHECKING"))
        if d.day == 10:
            rows.append(txn(1, "checking", d, "-600.00", "BROKERAGE CONTRIBUTION ACH"))
        if d.day == 12:
            rows.append(txn(1, "checking", d, "-140.00", "CITY UTILITIES ONLINE PAYMENT"))
        if d.day == 20:
            rows.append(txn(1, "checking", d, "-1250.00", "TEST BANK CARD AUTOPAY"))
        if d.day == 21:
            rows.append(txn(3, "credit_card", d, "1250.00", "AUTOPAY PAYMENT THANK YOU"))
        if d.day == 25:
            rows.append(txn(1, "checking", d, "-75.00", "ZELLE PAYMENT TO J DOE"))
        if d.day == 28:
            rows.append(txn(2, "savings", d, "7.50", "INTEREST PAID"))
        if (d - start).days % 3 == 0:
            rows.append(txn(3, "credit_card", d, "-82.35", "GROCERY MARKET"))
        if (d - start).days % 7 == 0:
            rows.append(txn(3, "credit_card", d, "-48.10", "FUEL STATION"))
        if d.day == 18:
            rows.append(txn(3, "credit_card", d, "-15.99", "STREAMING SERVICE"))
        d += timedelta(days=1)
    return rows


class TestRealisticHousehold:
    """Hand computation.

    Newest data is 13 Sep. Coverage runs from each account's first row to that date:
      checking from 20 May = 117 days; savings from 28 May = 109; card from 17 May = 120.

    Checking (excluding transfers):
      income   7 paydays × 3,100            = 21,700.00
      spending rent 4 × 1,900 = 7,600; utilities 4 × 140 = 560; Zelle 4 × 75 = 300
                                            =  8,460.00
      excluded: 4 × 500 to savings (paired), 4 × 1,250 card autopay (paired with the
                card's payment), 4 × 600 brokerage contributions (unpaired transfer)
    Savings:    income: interest 4 × 7.50   =     30.00 (the 500s are paired)
    Card:       groceries 40 × 82.35 = 3,294.00; fuel 18 × 48.10 = 865.80;
                streaming 4 × 15.99 = 63.96                = 4,223.76

    Monthly (cents × 30.4375 / days, rounded once):
      spending  checking 846000 × 30.4375 / 117 = 220086.5 → 2,200.87
                card     422376 × 30.4375 / 120 = 107133.9 → 1,071.34
                                                           = 3,272.21
      income    checking 2170000 × 30.4375 / 117 = 564524.6 → 5,645.25
                savings     3000 × 30.4375 / 109 =    837.7 →     8.38
                                                           = 5,653.63
    """

    def setup_method(self):
        self.summary = summarize(realistic_household())

    def test_monthly_spending(self):
        assert self.summary.monthly_spending == Money("3272.21")

    def test_monthly_income(self):
        assert self.summary.monthly_income == Money("5653.63")

    def test_transfers_between_own_accounts_are_paired(self):
        assert self.summary.paired_transfer_count == 8
        assert self.summary.paired_transfer_total == Money("7000.00")

    def test_brokerage_contributions_excluded_as_unpaired_transfers(self):
        assert self.summary.unpaired_transfers_out == Money("2400.00")
        assert self.summary.unpaired_transfers_in == Money(0)

    def test_unmatched_bill_payments_are_counted_and_reported(self):
        assert self.summary.unpaired_payments_counted == Money("560.00")

    def test_coverage(self):
        assert self.summary.first_date == date(2026, 5, 17)
        assert self.summary.last_date == AS_OF
        assert self.summary.shortest_coverage_days == 109


class TestClassification:
    def test_p2p_is_never_a_transfer_even_when_worded_as_one(self):
        assert classify("ZELLE TRANSFER TO LANDLORD", "checking", Money("-1500")) == "ordinary"

    def test_investment_contributions_are_transfers_not_spending(self):
        assert classify("BROKERAGE CONTRIBUTION ACH", "checking", Money("-600")) == "transfer"
        assert classify("ROTH IRA DEPOSIT", "checking", Money("-500")) == "transfer"

    def test_payment_received_on_a_card_is_not_a_refund(self):
        assert classify("AUTOPAY PAYMENT THANK YOU", "credit_card", Money("1250")) == "transfer"
        assert classify("REFUND GROCERY MARKET", "credit_card", Money("20")) == "ordinary"

    def test_card_payment_leaving_checking_is_flagged_but_counted(self):
        assert classify("TEST BANK CARD AUTOPAY", "checking", Money("-900")) == "card_payment"


class TestPairing:
    def test_coincidental_equal_amounts_do_not_cancel_out(self):
        a = txn(1, "checking", AS_OF, "-12.99", "STREAMING SERVICE")
        b = txn(2, "savings", AS_OF, "12.99", "REFUND STORE")
        assert match_transfers([a, b], 4) == set()

    def test_each_inflow_pairs_only_once(self):
        first = txn(1, "checking", AS_OF, "-500", "ONLINE TRANSFER TO SAVINGS")
        second = txn(1, "checking", AS_OF - timedelta(days=1), "-500", "ONLINE TRANSFER TO SAVINGS")
        inflow = txn(2, "savings", AS_OF, "500", "ONLINE TRANSFER FROM CHECKING")
        assert len(match_transfers([first, second, inflow], 4)) == 2

    def test_pairs_outside_the_window_do_not_match(self):
        out = txn(1, "checking", AS_OF - timedelta(days=5), "-500", "ONLINE TRANSFER TO SAVINGS")
        inflow = txn(2, "savings", AS_OF, "500", "ONLINE TRANSFER FROM CHECKING")
        assert match_transfers([out, inflow], 4) == set()

    def test_same_account_never_pairs_with_itself(self):
        out = txn(1, "checking", AS_OF, "-500", "TRANSFER")
        back = txn(1, "checking", AS_OF, "500", "TRANSFER REVERSAL")
        assert match_transfers([out, back], 4) == set()


class TestEdgeCases:
    def test_unpaired_card_payment_is_spending(self):
        """Card not imported: the payment is the only trace of its purchases.

        90 days. Spending 100000 cents × 30.4375 / 90 = 33819.4 → 338.19
                 Income   300000 cents × 30.4375 / 90 = 101458.3 → 1,014.58
        """
        start = AS_OF - timedelta(days=89)
        summary = summarize(
            [
                txn(1, "checking", start, "3000.00", "PAYROLL"),
                txn(1, "checking", AS_OF, "-1000.00", "TEST BANK CARD AUTOPAY"),
            ]
        )
        assert summary.monthly_spending == Money("338.19")
        assert summary.monthly_income == Money("1014.58")
        assert summary.unpaired_payments_counted == Money("1000.00")

    def test_card_refund_reduces_spending(self):
        """61 days; 100 spent, 20 refunded → 8000 cents × 30.4375 / 61 = 3991.8 → 39.92."""
        start = AS_OF - timedelta(days=60)
        summary = summarize(
            [
                txn(3, "credit_card", start, "-100.00", "GROCERY MARKET"),
                txn(3, "credit_card", AS_OF, "20.00", "REFUND GROCERY MARKET"),
            ]
        )
        assert summary.monthly_spending == Money("39.92")

    def test_exact_half_cent_rounds_up_rather_than_drifting(self):
        """90000 cents × 30.4375 / 90 = 30437.5 exactly → 304.38.

        A precomputed factor (30.4375 / 90, rounded to 28 digits) gives 30437.4999… → 304.37.
        """
        start = AS_OF - timedelta(days=89)
        summary = summarize(
            [
                txn(1, "checking", start, "-900.00", "RENT"),
                txn(1, "checking", AS_OF, "1.00", "INTEREST"),
            ]
        )
        assert summary.monthly_spending == Money("304.38")

    def test_sparse_account_is_measured_to_the_newest_data(self):
        """One interest credit must not be scaled as a one-day history (×30)."""
        start = AS_OF - timedelta(days=99)
        summary = summarize(
            [
                txn(1, "checking", start, "-10.00", "COFFEE"),
                txn(2, "savings", AS_OF - timedelta(days=9), "5.00", "INTEREST PAID"),
                txn(1, "checking", AS_OF, "-10.00", "COFFEE"),
            ]
        )
        savings = next(c for c in summary.coverage if c.account_id == 2)
        assert savings.days == 10
        # 500 cents × 30.4375 / 10 = 1521.9 → 15.22, not 5.00 × 30.4375
        assert summary.monthly_income == Money("15.22")

    def test_no_spending_accounts_means_no_summary(self):
        assert summarize([txn(9, "brokerage", AS_OF, "-100", "BUY VTI")]) is None
