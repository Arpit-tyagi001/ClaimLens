from datetime import date
from decimal import Decimal

import pytest
from docint.claimlens_docs.rules import (
    add_months,
    amount_within_limit,
    event_within_policy_period,
    waiting_period_elapsed,
    waiting_period_end,
)

# --- add_months: month-end and leap-day edge cases ---

@pytest.mark.parametrize("start, months, expected", [
    (date(2024, 1, 31), 1, date(2024, 2, 29)),    # month end, leap year
    (date(2025, 1, 31), 1, date(2025, 2, 28)),    # month end, normal year
    (date(2024, 2, 29), 12, date(2025, 2, 28)),   # leap day + 1 year
    (date(2024, 2, 29), 48, date(2028, 2, 29)),   # leap day + 4 years lands on a leap day
    (date(2024, 3, 31), 1, date(2024, 4, 30)),
    (date(2023, 11, 15), 3, date(2024, 2, 15)),   # crosses a year boundary
    (date(2023, 4, 1), 24, date(2025, 4, 1)),
])
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_waiting_period_end_units():
    start = date(2023, 4, 1)
    assert waiting_period_end(start, 30, "days") == date(2023, 5, 1)
    assert waiting_period_end(start, 24, "months") == date(2025, 4, 1)
    assert waiting_period_end(start, 2, "years") == date(2025, 4, 1)
    assert waiting_period_end(start, 1, "Year") == date(2024, 4, 1)


@pytest.mark.parametrize("unit", ["weeks", "fortnights", ""])
def test_waiting_period_end_rejects_unknown_unit(unit):
    with pytest.raises(ValueError):
        waiting_period_end(date(2024, 1, 1), 1, unit)


def test_waiting_period_end_rejects_negative():
    with pytest.raises(ValueError):
        waiting_period_end(date(2024, 1, 1), -1, "days")


# --- waiting_period_elapsed ---

def test_waiting_period_not_elapsed():
    # Synthetic case_01: cataract on 10/01/2025, 24 months from 01/04/2023.
    result = waiting_period_elapsed(date(2023, 4, 1), date(2025, 1, 10), 24, "months")
    assert result.passed is False
    assert "81 day(s) inside" in result.detail


def test_waiting_period_elapsed():
    # Synthetic case_02: initial 30 days long over by June 2024.
    result = waiting_period_elapsed(date(2023, 4, 1), date(2024, 6, 18), 30, "days")
    assert result.passed is True


def test_waiting_period_boundary_day_is_elapsed():
    # The first day after the waiting period is no longer inside it.
    assert waiting_period_elapsed(date(2023, 4, 1), date(2025, 4, 1), 24, "months").passed
    assert not waiting_period_elapsed(date(2023, 4, 1), date(2025, 3, 31), 24, "months").passed


def test_waiting_period_leap_day_start():
    # Policy starting on 29 Feb 2024 with a 1-year wait ends on 28 Feb 2025.
    assert waiting_period_elapsed(date(2024, 2, 29), date(2025, 2, 28), 1, "years").passed
    assert not waiting_period_elapsed(date(2024, 2, 29), date(2025, 2, 27), 1, "years").passed


# --- event_within_policy_period ---

@pytest.mark.parametrize("event, inside", [
    (date(2024, 4, 1), True),    # first day, inclusive
    (date(2025, 3, 31), True),   # last day, inclusive
    (date(2024, 10, 15), True),
    (date(2024, 3, 31), False),  # day before
    (date(2025, 4, 1), False),   # day after
])
def test_event_within_policy_period(event, inside):
    result = event_within_policy_period(event, date(2024, 4, 1), date(2025, 3, 31))
    assert result.passed is inside


def test_event_within_policy_period_rejects_reversed_period():
    with pytest.raises(ValueError):
        event_within_policy_period(date(2024, 5, 1), date(2025, 1, 1), date(2024, 1, 1))


# --- amount_within_limit ---

def test_amount_within_limit():
    assert amount_within_limit(Decimal("5000"), Decimal("5000")).passed  # equal is allowed
    assert amount_within_limit(Decimal("4999.99"), Decimal("5000")).passed


def test_amount_over_limit_reports_excess():
    result = amount_within_limit(Decimal("8000"), Decimal("5000"))
    assert result.passed is False
    assert "by Rs. 3,000" in result.detail


def test_amount_rejects_negative():
    with pytest.raises(ValueError):
        amount_within_limit(Decimal("-1"), Decimal("5000"))


def test_contract_signature_defaults_to_months():
    assert waiting_period_elapsed(date(2023, 4, 1), date(2025, 4, 1), 24).passed


def test_rule_result_contract_shape():
    out = waiting_period_elapsed(date(2023, 4, 1), date(2025, 1, 10), 24, "months").to_dict()
    assert set(out) == {"rule", "inputs", "result", "explanation"}
    assert out["result"] is False
    assert out["inputs"]["policy_start"] == "2023-04-01"
    assert "inside it" in out["explanation"]
