"""Show the deterministic rule checks on the synthetic cases.

    python -m eval.demo_rules
"""

from datetime import date
from decimal import Decimal

from docint.claimlens_docs.rules import amount_within_limit, event_within_policy_period, waiting_period_elapsed

INCEPTION = date(2023, 4, 1)
PERIOD = (date(2024, 4, 1), date(2025, 3, 31))

CHECKS = [
    ("case_01: Had the 24-month cataract wait ended?",
     waiting_period_elapsed(INCEPTION, date(2025, 1, 10), 24, "months")),
    ("case_01: Is the amount within the cataract sub-limit?", amount_within_limit(Decimal("45000"), Decimal("40000"))),
    ("case_02: Had the initial 30-day wait ended?", waiting_period_elapsed(INCEPTION, date(2024, 6, 18), 30, "days")),
    ("case_03: Had the 36-month pre-existing wait ended?",
     waiting_period_elapsed(INCEPTION, date(2024, 11, 12), 36, "months")),
    ("case_07: Is room rent within the daily limit?", amount_within_limit(Decimal("8000"), Decimal("5000"))),
    ("case_08: Was the admission inside the policy period?", event_within_policy_period(date(2024, 10, 6), *PERIOD)),
]

if __name__ == "__main__":
    for question, result in CHECKS:
        answer = "YES" if result.passed else "NO "
        print(f"[{answer}] {question}\n      {result.detail}\n")
