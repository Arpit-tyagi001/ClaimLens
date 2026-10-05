"""End-to-end on real PDFs rendered from the synthetic documents."""

from datetime import date

import pytest

pytest.importorskip("pymupdf")

from docint.claimlens_docs import extract_policy_facts, extract_rejection, ingest_document, locate_quote  # noqa: E402
from docint.claimlens_docs.ingest import document_text  # noqa: E402

from eval.make_pdfs import render_text_pdf  # noqa: E402
from eval.run import CASES_DIR, POLICY_PATH  # noqa: E402


@pytest.fixture(scope="module")
def policy_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "policy.pdf"
    return render_text_pdf(POLICY_PATH.read_text(encoding="utf-8"), path)


@pytest.fixture(scope="module")
def policy(policy_pdf):
    return ingest_document(str(policy_pdf), "pol-pdf", "policy")


def test_policy_pdf_is_parsed_into_clause_chunks(policy):
    assert policy.doc_type == "policy"
    assert policy.has_text_layer
    assert policy.pages_without_text == []
    paths = [c.section_path for c in policy.chunks]
    assert "4 Waiting Periods > 4.2" in paths
    assert "6 Limits And Sub-Limits > 6.2" in paths


def test_chunk_bboxes_are_real_page_coordinates(policy):
    for chunk in policy.chunks:
        x0, y0, x1, y1 = chunk.bbox
        assert 0 <= x0 < x1 <= 595 and 0 <= y0 < y1 <= 842
    first, second = policy.chunks[1], policy.chunks[2]
    assert first.bbox[1] < second.bbox[1]  # reading order, top to bottom


def test_wrapped_pdf_lines_become_one_clause(policy):
    clause = next(c for c in policy.chunks if c.section_path.endswith("4.2"))
    assert clause.text.endswith("after the date of inception of the first policy.")


def test_policy_facts_from_pdf(policy):
    facts = extract_policy_facts(policy)
    assert facts.policy_start == date(2024, 4, 1)
    assert facts.policy_end == date(2025, 3, 31)
    assert facts.first_inception_date == date(2023, 4, 1)
    assert facts.sum_insured == 500000
    waits = {w.kind: w for w in facts.waiting_periods}
    assert waits["specified_disease"].months == 24
    assert waits["pre_existing"].months == 36
    assert waits["initial"].days == 30 and waits["initial"].months == 1
    clause_42 = next(c for c in policy.chunks if c.section_path.endswith("4.2"))
    assert waits["specified_disease"].chunk_id == clause_42.chunk_id


def test_grounding_on_pdf_returns_pdf_bbox(policy):
    r = locate_quote("pol-pdf", "Cataract treatment is subject to a sub-limit of Rs. 40,000/- per eye")
    assert r.found and r.page == 1
    assert r.bbox[0] > 0  # a real coordinate, not the plain-text placeholder


def test_letter_reasons_get_page_and_bbox(tmp_path):
    letter = (CASES_DIR / "case_01" / "letter.txt").read_text(encoding="utf-8")
    doc = ingest_document(str(render_text_pdf(letter, tmp_path / "l.pdf")), "let", "letter")
    result = extract_rejection(doc)
    assert result.claim_no == "CLM/2025/0001"
    assert result.admission_date == date(2025, 1, 10)
    assert result.claimed_amount == 45000.0
    assert [r.reason_id for r in result.reasons] == ["r1", "r2"]
    assert [r.category for r in result.reasons] == ["waiting_period", "limit_exceeded"]
    r1, r2 = result.reasons
    assert r1.page == 1 and r1.bbox[1] < r2.bbox[1]
    assert r1.bbox[3] - r1.bbox[1] > 20  # the wrapped reason covers two lines
    assert r1.clause_refs == ["4.2"]


def test_letter_paragraphs_are_kept_apart(tmp_path):
    doc = ingest_document(str(render_text_pdf("Line one\nLine two\n\nNew paragraph", tmp_path / "p.pdf")),
                          "p", "letter")
    assert document_text(doc)[0] == "Line one\nLine two\n\nNew paragraph"


def test_scanned_pages_are_reported(tmp_path):
    doc = ingest_document(str(render_text_pdf("Some text", tmp_path / "s.pdf", blank_pages=2)), "s", "policy")
    assert doc.n_pages == 3
    assert doc.has_text_layer
    assert doc.pages_without_text == [2, 3]


def test_pdf_with_no_text_layer(tmp_path):
    doc = ingest_document(str(render_text_pdf("", tmp_path / "e.pdf")), "e", "policy")
    assert doc.has_text_layer is False
    assert doc.chunks == []


def test_unknown_doc_type(tmp_path):
    with pytest.raises(ValueError):
        ingest_document(str(render_text_pdf("x", tmp_path / "x.pdf")), "x", "invoice")
