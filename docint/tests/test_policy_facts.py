from datetime import date
from decimal import Decimal

import pytest
from docint.claimlens_docs.policy_facts import apply_corrections, extract_policy_facts

from eval.run import POLICY_PATH

POLICY = POLICY_PATH.read_text(encoding="utf-8")


def test_schedule_facts():
    facts = extract_policy_facts(POLICY)
    assert facts.policy_number.value == "NNH/2024/100001"
    assert facts.first_inception_date.value == date(2023, 4, 1)
    assert facts.period_start.value == date(2024, 4, 1)
    assert facts.period_end.value == date(2025, 3, 31)
    assert facts.sum_insured.value == Decimal("500000")


def test_waiting_periods():
    facts = extract_policy_facts(POLICY)
    assert (facts.initial_waiting.value, facts.initial_waiting.unit) == (30, "days")
    assert (facts.specified_disease_waiting.value, facts.specified_disease_waiting.unit) == (24, "months")
    assert (facts.pre_existing_waiting.value, facts.pre_existing_waiting.unit) == (36, "months")


def test_room_rent_percentage_uses_sum_insured():
    facts = extract_policy_facts(POLICY)
    assert facts.room_rent_limit_per_day.value == Decimal("5000")


def test_room_rent_fixed_amount():
    facts = extract_policy_facts("Room rent is limited to Rs. 4,000/- per day.")
    assert facts.room_rent_limit_per_day.value == Decimal("4000")


def test_every_fact_points_back_to_its_source():
    facts = extract_policy_facts(POLICY)
    snippet = POLICY[facts.pre_existing_waiting.start:facts.pre_existing_waiting.end]
    assert "36 months" in snippet


def test_missing_facts_are_none():
    facts = extract_policy_facts("A policy document with no recognisable facts.")
    assert facts.sum_insured is None
    assert facts.pre_existing_waiting is None


def test_user_corrections_are_marked():
    facts = apply_corrections(extract_policy_facts(POLICY), {"pre_existing_waiting": 48})
    assert facts.pre_existing_waiting.value == 48
    assert facts.pre_existing_waiting.confirmed_by_user is True


def test_correction_can_fill_a_missing_fact():
    facts = apply_corrections(extract_policy_facts("nothing here"), {"sum_insured": Decimal("300000")})
    assert facts.sum_insured.value == Decimal("300000")


def test_unknown_correction_is_rejected():
    with pytest.raises(KeyError):
        apply_corrections(extract_policy_facts(POLICY), {"favourite_colour": "blue"})
