"""Import tests.

The assertions here are about silent corruption, not crashes. An importer that
throws is annoying; one that quietly double-counts a month of spending produces a
wrong number that looks entirely reasonable.
"""

from __future__ import annotations

from datetime import date

import pytest

from financial_advisor.importers.csv_import import (
    ImportError_,
    SignConvention,
    detect_columns,
    import_file,
    infer_day_first,
    parse_date,
)
from financial_advisor.money import Money, NonUSDCurrencyError

from .conftest import FIXTURES


def _import(conn, account, filename, **kwargs):
    return import_file(
        conn,
        FIXTURES / filename,
        account_id=account.id,
        account_name=account.name,
        is_liability=account.is_liability,
        **kwargs,
    )


class TestColumnDetection:
    def test_standard_headers(self):
        cols = detect_columns(["Date", "Description", "Amount"])
        assert (cols.date, cols.description, cols.amount) == ("Date", "Description", "Amount")

    def test_alternate_names(self):
        cols = detect_columns(["Posted Date", "Payee", "Debit", "Credit"])
        assert cols.date == "Posted Date" and cols.debit == "Debit"

    def test_case_and_punctuation_insensitive(self):
        cols = detect_columns(["TRANSACTION_DATE", "original description", "Amount"])
        assert cols.date == "TRANSACTION_DATE"
        assert cols.description == "original description"

    def test_missing_columns_explain_what_and_how(self):
        with pytest.raises(ImportError_) as exc:
            detect_columns(["Foo", "Bar"])
        message = str(exc.value)
        assert "date column" in message
        assert "Headers seen" in message   # so the user can see what was actually there


class TestDates:
    def test_iso(self):
        assert parse_date("2026-01-05") == date(2026, 1, 5)

    def test_us_month_first_default(self):
        assert parse_date("01/05/2026") == date(2026, 1, 5)

    def test_day_first_when_inferred(self):
        assert parse_date("25/01/2026", day_first=True) == date(2026, 1, 25)

    def test_two_digit_year(self):
        assert parse_date("01/05/26") == date(2026, 1, 5)

    def test_inference_is_file_wide_not_per_row(self):
        # A file with 25/01 must not read 03/02 as March 2nd.
        assert infer_day_first(["25/01/2026", "03/02/2026"]) is True
        assert infer_day_first(["01/05/2026", "02/06/2026"]) is False

    def test_unrecognized_raises(self):
        with pytest.raises(ImportError_):
            parse_date("not a date")


class TestBasicImport:
    def test_inserts_rows(self, db, checking):
        result = _import(db, checking, "bank_simple.csv")
        assert (result.rows_read, result.inserted, result.duplicates) == (4, 4, 0)

    def test_amounts_and_signs(self, db, checking):
        _import(db, checking, "bank_simple.csv")
        rows = db.execute(
            "SELECT posted_on, amount_cents FROM txn ORDER BY posted_on"
        ).fetchall()
        assert rows[0]["amount_cents"] == -450
        assert rows[1]["amount_cents"] == 320000

    def test_date_range_reported(self, db, checking):
        result = _import(db, checking, "bank_simple.csv")
        assert result.date_range == (date(2026, 1, 5), date(2026, 1, 8))

    def test_preamble_rows_skipped(self, db, checking):
        result = _import(db, checking, "with_preamble.csv")
        assert result.inserted == 2

    def test_semicolon_delimiter(self, db, checking):
        result = _import(db, checking, "semicolon.csv")
        assert result.inserted == 2

    def test_debit_credit_columns(self, db, checking):
        _import(db, checking, "debit_credit.csv")
        amounts = [r["amount_cents"] for r in db.execute(
            "SELECT amount_cents FROM txn ORDER BY posted_on")]
        # Debits are money out, credits money in — regardless of how the file signs them.
        assert amounts == [-5820, 2500, -7999]

    def test_day_first_file(self, db, checking):
        _import(db, checking, "day_first.csv")
        dates = [r["posted_on"] for r in db.execute("SELECT posted_on FROM txn ORDER BY id")]
        assert dates == ["2026-01-25", "2026-02-03"]


class TestIdempotencyAndDedupe:
    """The double-counting failure mode."""

    def test_same_file_twice_is_a_noop(self, db, checking):
        first = _import(db, checking, "bank_simple.csv")
        second = _import(db, checking, "bank_simple.csv")
        assert first.inserted == 4
        assert second.skipped_file is True
        assert db.execute("SELECT COUNT(*) c FROM txn").fetchone()["c"] == 4

    def test_overlapping_export_does_not_duplicate(self, db, checking):
        _import(db, checking, "bank_simple.csv")      # Jan 5-8
        result = _import(db, checking, "bank_overlap.csv")  # Jan 7-12, 2 shared rows
        assert result.duplicates == 2
        assert result.inserted == 2
        assert db.execute("SELECT COUNT(*) c FROM txn").fetchone()["c"] == 6

    def test_genuine_same_day_repeats_are_all_kept(self, db, checking):
        # Three identical coffees on one day are three transactions, not one.
        # This is the case a naive hash silently collapses.
        result = _import(db, checking, "bank_repeats.csv")
        assert result.inserted == 3
        assert result.duplicates == 0

    def test_repeats_still_dedupe_on_reimport(self, db, checking):
        _import(db, checking, "bank_repeats.csv")
        db.execute("DELETE FROM import_run")   # force a re-read of the same content
        result = _import(db, checking, "bank_repeats.csv")
        assert result.inserted == 0
        assert result.duplicates == 3

    def test_same_file_into_two_accounts_is_allowed(self, db, checking, card):
        # Dedupe is per-account; the same statement legitimately imports to each.
        _import(db, checking, "bank_simple.csv")
        result = _import(db, card, "bank_simple.csv")
        assert result.inserted == 4


class TestCurrencyEnforcement:
    """§12.1 — refuse rather than coerce."""

    def test_foreign_currency_rejected(self, db, checking):
        with pytest.raises(NonUSDCurrencyError):
            _import(db, checking, "foreign_currency.csv")

    def test_nothing_persisted_after_rejection(self, db, checking):
        with pytest.raises(NonUSDCurrencyError):
            _import(db, checking, "foreign_currency.csv")
        assert db.execute("SELECT COUNT(*) c FROM txn").fetchone()["c"] == 0


class TestSignConvention:
    def test_flipped_inverts_amounts(self, db, card):
        _import(db, card, "card_positive.csv", sign=SignConvention.FLIPPED)
        rows = db.execute("SELECT amount_cents FROM txn ORDER BY posted_on").fetchall()
        assert rows[0]["amount_cents"] == -6430   # a purchase is money out
        assert rows[-1]["amount_cents"] == 20000  # the payment is money in

    def test_liability_with_mostly_positive_amounts_warns(self, db, card):
        result = _import(db, card, "card_positive.csv", sign=SignConvention.NATURAL)
        assert any("positive on a liability" in w for w in result.warnings)

    def test_warning_is_not_silent_auto_correction(self, db, card):
        # Guessing wrong turns debt into an asset, so the data is left as given
        # and the ambiguity is surfaced instead.
        _import(db, card, "card_positive.csv", sign=SignConvention.NATURAL)
        first = db.execute("SELECT amount_cents FROM txn ORDER BY posted_on").fetchone()
        assert first["amount_cents"] == 6430


class TestFailureIsAtomic:
    def test_malformed_row_aborts_whole_file(self, db, checking):
        with pytest.raises(ImportError_) as exc:
            _import(db, checking, "malformed.csv")
        assert "line" in str(exc.value)   # points at the offending row

    def test_no_partial_rows_after_failure(self, db, checking):
        with pytest.raises(ImportError_):
            _import(db, checking, "malformed.csv")
        # A half-loaded account would make the next run's dedupe compare against a
        # corrupt baseline, compounding the error.
        assert db.execute("SELECT COUNT(*) c FROM txn").fetchone()["c"] == 0

    def test_failed_file_can_be_retried_after_fixing(self, db, checking):
        with pytest.raises(ImportError_):
            _import(db, checking, "malformed.csv")
        result = _import(db, checking, "bank_simple.csv")
        assert result.inserted == 4


class TestMoneyIntegration:
    def test_amounts_round_trip_exactly(self, db, checking):
        _import(db, checking, "bank_simple.csv")
        rows = db.execute("SELECT amount_cents FROM txn")
        total = sum((Money.from_cents(r["amount_cents"]) for r in rows), Money(0))
        assert total == Money("2966.17")
