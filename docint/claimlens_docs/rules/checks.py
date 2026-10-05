"""Deterministic rule checks.

The LLM never does date arithmetic or limit comparisons. These functions do,
and each returns a RuleResult with a plain-English explanation that the
verifier and the UI can show next to a finding.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    detail: str
    inputs: dict = field(default_factory=dict, compare=False, hash=False)

    def to_dict(self) -> dict:
        """Contract 4.5 shape: {rule, inputs, result, explanation}, JSON friendly."""
        return {
            "rule": self.rule,
            "inputs": {k: str(v) for k, v in self.inputs.items()},
            "result": self.passed,
            "explanation": self.detail,
        }


def add_months(start: date, months: int) -> date:
    """Add calendar months, clamping to the last day of the target month.

    31 Jan + 1 month -> 28/29 Feb; 29 Feb 2024 + 12 months -> 28 Feb 2025.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def waiting_period_end(policy_start: date, amount: int, unit: str) -> date:
    """First date on which a claim is no longer inside the waiting period."""
    unit = unit.lower().rstrip("s")  # "days" -> "day", "years" -> "year"
    if amount < 0:
        raise ValueError("Waiting period cannot be negative")
    if unit == "day":
        return policy_start + timedelta(days=amount)
    if unit == "month":
        return add_months(policy_start, amount)
    if unit == "year":
        return add_months(policy_start, amount * 12)
    raise ValueError(f"Unknown waiting-period unit: {unit!r}")


def waiting_period_elapsed(
    policy_start: date, event_date: date, amount: int, unit: str = "months"
) -> RuleResult:
    """Has the waiting period finished by the date of treatment or admission?"""
    end = waiting_period_end(policy_start, amount, unit)
    passed = event_date >= end
    if passed:
        detail = (
            f"Waiting period of {amount} {unit} from {policy_start:%d %b %Y} "
            f"ended on {end:%d %b %Y}; the event on {event_date:%d %b %Y} is after it."
        )
    else:
        days_short = (end - event_date).days
        detail = (
            f"Waiting period of {amount} {unit} from {policy_start:%d %b %Y} "
            f"runs until {end:%d %b %Y}; the event on {event_date:%d %b %Y} is "
            f"{days_short} day(s) inside it."
        )
    inputs = {"policy_start": policy_start, "event_date": event_date, "amount": amount, "unit": unit}
    return RuleResult("waiting_period_elapsed", passed, detail, inputs)


def event_within_policy_period(
    event_date: date, period_start: date, period_end: date
) -> RuleResult:
    """Did the event happen inside the policy period? Both ends are inclusive."""
    if period_end < period_start:
        raise ValueError("Policy period ends before it starts")
    passed = period_start <= event_date <= period_end
    where = "inside" if passed else "outside"
    detail = (
        f"Event on {event_date:%d %b %Y} is {where} the policy period "
        f"{period_start:%d %b %Y} to {period_end:%d %b %Y}."
    )
    inputs = {"event_date": event_date, "period_start": period_start, "period_end": period_end}
    return RuleResult("event_within_policy_period", passed, detail, inputs)


def amount_within_limit(claimed: Decimal, limit: Decimal) -> RuleResult:
    """Is the claimed amount at or below the limit or sub-limit?"""
    claimed, limit = Decimal(claimed), Decimal(limit)
    if claimed < 0 or limit < 0:
        raise ValueError("Amounts cannot be negative")
    passed = claimed <= limit
    if passed:
        detail = f"Claimed Rs. {claimed:,} is within the limit of Rs. {limit:,}."
    else:
        detail = (
            f"Claimed Rs. {claimed:,} exceeds the limit of Rs. {limit:,} "
            f"by Rs. {claimed - limit:,}."
        )
    return RuleResult("amount_within_limit", passed, detail, {"claimed": claimed, "limit": limit})
