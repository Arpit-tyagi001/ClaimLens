from datetime import date
from decimal import Decimal

import pytest

from docint.claimlens_docs.letter import (
    EXCLUSION,
    LIMIT_EXCEEDED,
    MISSING_DOCUMENTS,
    NEEDS_HUMAN,
    PRE_EXISTING,
    WAITING_PERIOD,
    classify_reason,
    extract_letter,
)

LETTER = """Date: 20/01/2025
Claim No: CLM/2025/0001
Policy No: NNH/2024/100001
Date of Admission: 10/01/2025
Date of Discharge: 11/01/2025
Amount Claimed: Rs. 45,000/-

We regret that your claim is repudiated for the following reason(s):

1. Cataract treatment falls within the 24 months waiting period under Clause 4.2.
2. The amount is above the sub-limit of Rs. 40,000/- under Clause 6.2.

Yours sincerely,
"""


def test_extracts_header_fields():
    result = extract_letter(LETTER)
    assert result.claim_number == "CLM/2025/0001"
    assert result.policy_number == "NNH/2024/100001"
    assert result.letter_date == date(2025, 1, 20)
    assert result.admission_date == date(2025, 1, 10)
    assert result.discharge_date == date(2025, 1, 11)
    assert result.amount_claimed == Decimal("45000")


def test_extracts_numbered_reasons_with_exact_spans():
    reasons = extract_letter(LETTER).reasons
    assert [r.category for r in reasons] == [WAITING_PERIOD, LIMIT_EXCEEDED]
    for r in reasons:
        assert LETTER[r.start:r.end] == r.text  # span points at the real text
    assert reasons[0].text.startswith("Cataract")
    assert reasons[0].clause_refs == ["4.2"]


def test_reasons_separated_by_blank_lines():
    letter = "Reasons:\n\n1. Waiting period applies.\n\n2. Room rent limit exceeded.\n\nRegards"
    assert [r.category for r in extract_letter(letter).reasons] == [WAITING_PERIOD, LIMIT_EXCEEDED]


def test_fallback_to_trigger_sentences_without_a_list():
    letter = "Dear Sir,\nYour claim has been rejected as cosmetic surgery is excluded. Regards."
    reasons = extract_letter(letter).reasons
    assert len(reasons) == 1
    assert reasons[0].category == EXCLUSION


def test_missing_fields_stay_empty_instead_of_guessing():
    result = extract_letter("Hello, this letter has no details.")
    assert result.claim_number is None
    assert result.admission_date is None
    assert result.reasons == []


def test_unparseable_date_is_left_empty():
    result = extract_letter("Date of Admission: 31/02/2024")
    assert result.admission_date is None


@pytest.mark.parametrize("text, category", [
    ("Pre-existing disease waiting period of 36 months applies", PRE_EXISTING),
    ("Excluded as PED", PRE_EXISTING),
    ("Treatment within the 30 day waiting period", WAITING_PERIOD),
    ("The discharge summary has not been submitted", MISSING_DOCUMENTS),
    ("Required documents were not received", MISSING_DOCUMENTS),
    ("Room rent exceeds the eligible amount", LIMIT_EXCEEDED),
    ("Claim exceeds the cataract sub-limit", LIMIT_EXCEEDED),
    ("Cosmetic surgery is a permanent exclusion", EXCLUSION),
    ("This treatment is not covered", EXCLUSION),
    ("Non-disclosure of material facts", NEEDS_HUMAN),
])
def test_classify_reason(text, category):
    assert classify_reason(text) == category


def test_to_dict_is_json_friendly():
    d = extract_letter(LETTER).to_dict()
    assert d["admission_date"] == "2025-01-10"
    assert d["amount_claimed"] == "45000"
    assert d["reasons"][0]["category"] == WAITING_PERIOD
