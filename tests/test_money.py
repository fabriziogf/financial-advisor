"""Money is the foundation; these tests are correspondingly paranoid.

Every assertion here maps to a way a financial figure can go silently wrong.
"""

from decimal import Decimal

import pytest

from financial_advisor.money import (
    Money,
    MoneyParseError,
    NonUSDCurrencyError,
    parse_currency_code,
)


class TestFloatRejection:
    """R2 — the whole reason this type exists."""

    def test_construction_from_float_raises(self):
        with pytest.raises(TypeError, match="float"):
            Money(12.34)

    def test_multiplication_by_float_raises(self):
        with pytest.raises(TypeError, match="float"):
            Money("10.00") * 1.5

    def test_error_message_names_the_fix(self):
        # A refusal that doesn't say what to do instead just gets worked around.
        with pytest.raises(TypeError) as exc:
            Money(1.1)
        assert "Decimal" in str(exc.value)

    def test_bool_is_not_an_int_here(self):
        with pytest.raises(TypeError):
            Money(True)


class TestExactness:
    def test_classic_float_error_does_not_occur(self):
        assert Money("0.1") + Money("0.2") == Money("0.30")

    def test_repeated_addition_stays_exact(self):
        total = sum((Money("0.01") for _ in range(1000)), Money(0))
        assert total == Money("10.00")

    def test_decimal_input_is_exact(self):
        assert Money(Decimal("1234.56")).cents == 123456

    def test_int_is_whole_dollars(self):
        assert Money(5).cents == 500


class TestRounding:
    def test_half_cent_rounds_up(self):
        assert Money("0.005").cents == 1

    def test_sub_cent_precision_is_quantized(self):
        # Interest lines sometimes carry more precision than cents.
        assert Money("1.234").cents == 123
        assert Money("1.235").cents == 124

    def test_negative_half_cent(self):
        assert Money("-0.005").cents == -1


class TestParsing:
    @pytest.mark.parametrize(
        "raw,cents",
        [
            ("1234.56", 123456),
            ("$1,234.56", 123456),
            ("USD 1,234.56", 123456),
            ("-1234.56", -123456),
            ("$-1,234.56", -123456),
            ("(1,234.56)", -123456),        # accounting negative
            ("($1,234.56)", -123456),
            ("1234.56-", -123456),          # trailing sign, fixed-width exports
            ("−1234.56", -123456),     # unicode minus
            ("  42  ", 4200),
            ("0.00", 0),
            (".50", 50),
        ],
    )
    def test_real_world_formats(self, raw, cents):
        assert Money.parse(raw).cents == cents

    @pytest.mark.parametrize("raw", ["", "   ", None, "abc", "1.2.3", "$", "--5"])
    def test_unparseable_raises_rather_than_defaulting_to_zero(self, raw):
        # A silent zero is indistinguishable from a real zero balance.
        with pytest.raises(MoneyParseError):
            Money.parse(raw)

    def test_non_finite_rejected(self):
        with pytest.raises(MoneyParseError):
            Money(Decimal("NaN"))


class TestCurrency:
    """§12.1 — the USD invariant is enforced, not assumed."""

    def test_usd_passes(self):
        assert parse_currency_code("usd") == "USD"

    def test_missing_code_defaults_to_usd(self):
        # Most US exports omit the column; rejecting that would block normal files.
        assert parse_currency_code(None) == "USD"
        assert parse_currency_code("") == "USD"

    @pytest.mark.parametrize("code", ["EUR", "BRL", "GBP", "gbp"])
    def test_foreign_currency_raises(self, code):
        with pytest.raises(NonUSDCurrencyError):
            parse_currency_code(code)

    def test_error_explains_the_alternative(self):
        with pytest.raises(NonUSDCurrencyError) as exc:
            parse_currency_code("EUR")
        assert "declared asset" in str(exc.value)


class TestArithmetic:
    def test_sum_of_list_works(self):
        assert sum([Money("1.00"), Money("2.50")], Money(0)) == Money("3.50")

    def test_builtin_sum_without_start(self):
        assert sum([Money("1.00"), Money("2.50")]) == Money("3.50")

    def test_cannot_add_bare_number(self):
        with pytest.raises(TypeError):
            Money("1.00") + 5

    def test_multiply_by_share_count(self):
        assert Money("12.34") * 10 == Money("123.40")

    def test_multiply_by_decimal_quantity(self):
        assert Money("100.00") * Decimal("2.5") == Money("250.00")

    def test_ratio_returns_decimal_not_money(self):
        r = Money("25.00").ratio_to(Money("100.00"))
        assert isinstance(r, Decimal)
        assert r == Decimal("0.25")

    def test_ratio_against_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            Money("1.00").ratio_to(Money(0))

    def test_negation_and_abs(self):
        assert -Money("5.00") == Money("-5.00")
        assert abs(Money("-5.00")) == Money("5.00")


class TestComparisonAndDisplay:
    def test_ordering(self):
        assert Money("1.00") < Money("2.00")
        assert Money("2.00") >= Money("2.00")

    def test_equality_is_not_true_across_types(self):
        assert Money("1.00") != Decimal("1.00")

    def test_hashable(self):
        assert len({Money("1.00"), Money("1.00"), Money("2.00")}) == 2

    def test_zero_is_falsy(self):
        assert not Money(0)
        assert Money("0.01")

    @pytest.mark.parametrize(
        "value,shown",
        [
            ("1234.56", "$1,234.56"),
            ("-1234.56", "-$1,234.56"),
            ("0", "$0.00"),
            ("1000000", "$1,000,000.00"),
            ("0.07", "$0.07"),
        ],
    )
    def test_formatting(self, value, shown):
        assert Money(value).format() == shown

    def test_amount_property_is_decimal(self):
        assert Money("1234.56").amount == Decimal("1234.56")
