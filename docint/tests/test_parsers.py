from datetime import date
from decimal import Decimal

import pytest

from docint.claimlens_docs.parsers import parse_indian_date, parse_inr_amount


@pytest.mark.parametrize("text, expected", [
    ("05/03/2025", date(2025, 3, 5)),      # day first: 5 March, not May 3
    ("5-3-2025", date(2025, 3, 5)),
    ("05.03.2025", date(2025, 3, 5)),
    ("05/03/25", date(2025, 3, 5)),
    ("5 March 2025", date(2025, 3, 5)),
    ("05-Mar-2025", date(2025, 3, 5)),
    ("5th Mar, 2025", date(2025, 3, 5)),
    ("1st Sept 2024", date(2024, 9, 1)),
    ("2025-03-05", date(2025, 3, 5)),
    ("29/02/2024", date(2024, 2, 29)),     # leap day exists in 2024
])
def test_parse_indian_date(text, expected):
    assert parse_indian_date(text) == expected


@pytest.mark.parametrize("text", [
    "29/02/2025",   # 2025 is not a leap year
    "31/04/2024",   # April has 30 days
    "13/13/2024",
    "05 Foo 2025",
    "yesterday",
    "",
])
def test_parse_indian_date_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_indian_date(text)


@pytest.mark.parametrize("text, expected", [
    ("Rs. 1,25,000/-", Decimal("125000")),
    ("Rs 1,25,000", Decimal("125000")),
    ("₹ 50,000.50", Decimal("50000.50")),
    ("INR 2,00,000", Decimal("200000")),
    ("rs.40,000/-", Decimal("40000")),
    ("5000", Decimal("5000")),
])
def test_parse_inr_amount(text, expected):
    assert parse_inr_amount(text) == expected


@pytest.mark.parametrize("text", ["Rs. abc", "", "-5000", "Rs. 1,25,000.123"])
def test_parse_inr_amount_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_inr_amount(text)
