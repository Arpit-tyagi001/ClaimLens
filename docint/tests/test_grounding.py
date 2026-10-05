import pytest

from docint.claimlens_docs.grounding import locate_quote, normalise, quote_in_text
from docint.claimlens_docs.ingest import ingest_text
from eval.run import POLICY_PATH

POLICY_TEXT = POLICY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def policy():
    return ingest_text(POLICY_TEXT, "pol-test", "policy")


def test_normalise_drops_punctuation_and_digit_grouping():
    assert normalise("Rs. 1,25,000/- “per eye”") == "rs 125000 per eye"


def test_exact_quote_found_with_page_bbox_and_chunk(policy):
    r = locate_quote(policy, "Cataract treatment is subject to a sub-limit of Rs. 40,000/- per eye.")
    assert r.found and r.match_score == 1.0
    assert r.page == 1
    assert r.bbox is not None and len(r.bbox) == 4
    assert r.chunk_id == next(c.chunk_id for c in policy.chunks if c.section_path.endswith("6.2"))


def test_lookup_by_registered_doc_id(policy):
    assert locate_quote("pol-test", "Room rent is limited to 1% of the Sum Insured per day").found


def test_unknown_doc_id_raises():
    with pytest.raises(KeyError):
        locate_quote("never-registered", "anything")


def test_small_typo_is_still_found(policy):
    r = locate_quote(policy, "Surgical treatmnet of obesity or weight control programmes.")
    assert r.found
    assert 0.9 <= r.match_score < 1.0


def test_changed_number_is_rejected_with_a_reason(policy):
    r = locate_quote(policy, "Cataract treatment is subject to a sub-limit of Rs. 80,000/- per eye.")
    assert not r.found
    assert r.match_score > 0.9  # very similar text...
    assert "80000" in r.reason and "40000" in r.reason  # ...but the number differs


def test_flipped_meaning_is_rejected(policy):
    r = locate_quote(policy, "may reject the claim if the document is still not received 15 days after that reminder")
    assert not r.found
    assert "only" in r.reason


def test_invented_clause_is_rejected(policy):
    r = locate_quote(policy, "All treatment taken outside a network hospital is permanently excluded.")
    assert not r.found
    assert r.match_score < 0.9


def test_quote_spanning_two_clauses_uses_union_bbox(policy):
    r = locate_quote(policy, "programmes. 5.3 Expenses for spectacles")
    assert r.found
    c52 = next(c for c in policy.chunks if c.section_path.endswith("5.2"))
    c53 = next(c for c in policy.chunks if c.section_path.endswith("5.3"))
    assert r.bbox == [min(c52.bbox[0], c53.bbox[0]), c52.bbox[1], max(c52.bbox[2], c53.bbox[2]), c53.bbox[3]]


def test_empty_quote():
    assert not locate_quote(ingest_text("x", "e", "policy"), "  ").found


def test_eval_adapter():
    assert quote_in_text("Expenses for spectacles, contact lenses and hearing aids.", POLICY_TEXT)
    assert not quote_in_text("Expenses for spectacles are fully covered.", POLICY_TEXT)
