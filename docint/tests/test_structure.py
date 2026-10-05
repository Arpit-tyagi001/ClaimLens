from docint.claimlens_docs.structure import DEFINITION, EXCLUSION, WAITING_PERIOD, Line, build_chunks, lines_from_text
from eval.run import POLICY_PATH

POLICY = """SAMPLE POLICY

SECTION 3. DEFINITIONS
3.1 Pre-existing Disease means any condition diagnosed within 48 months.

SECTION 4. WAITING PERIODS
4.2 Specified disease waiting period: cataract shall be excluded
until the expiry of 24 months.
4.2.1 Hernia is also covered by 4.2.

5. EXCLUSIONS
5.1 Cosmetic surgery.

This paragraph has no number.
"""


def chunks():
    return build_chunks(lines_from_text(POLICY), "pol")


def test_one_chunk_per_clause_with_section_path():
    by_path = {c.section_path: c for c in chunks()}
    assert "3 Definitions > 3.1" in by_path
    assert "4 Waiting Periods > 4.2" in by_path
    assert "4 Waiting Periods > 4.2 > 4.2.1" in by_path
    assert "5 Exclusions > 5.1" in by_path


def test_wrapped_lines_join_the_clause():
    clause = next(c for c in chunks() if c.section_path == "4 Waiting Periods > 4.2")
    assert clause.text.endswith("until the expiry of 24 months.")


def test_tags():
    by_path = {c.section_path: c for c in chunks()}
    assert DEFINITION in by_path["3 Definitions > 3.1"].tags
    assert {WAITING_PERIOD, EXCLUSION} <= set(by_path["4 Waiting Periods > 4.2"].tags)
    assert EXCLUSION in by_path["5 Exclusions > 5.1"].tags


def test_unnumbered_paragraph_belongs_to_the_section():
    last = chunks()[-1]
    assert last.text == "This paragraph has no number."
    assert last.section_path == "5 Exclusions"


def test_chunk_ids_are_unique_and_stable():
    ids = [c.chunk_id for c in chunks()]
    assert len(ids) == len(set(ids))
    assert ids == [c.chunk_id for c in chunks()]


def test_heading_found_by_font_size():
    lines = [
        Line(1, "Benefits Covered", [50, 50, 200, 66], size=14),
        Line(1, "1.1 In-patient treatment is covered.", [50, 70, 400, 82], size=10),
        Line(1, "1.2 Day care is covered.", [50, 84, 400, 96], size=10),
    ]
    paths = [c.section_path for c in build_chunks(lines, "d")]
    assert paths == ["Benefits Covered > 1.1", "Benefits Covered > 1.2"]


def test_clause_split_across_pages_keeps_one_page_per_chunk():
    lines = [
        Line(1, "4.1 Expenses within 30 days", [50, 780, 400, 792], size=10),
        Line(2, "are excluded.", [50, 50, 200, 62], size=10),
    ]
    result = build_chunks(lines, "d")
    assert [c.page for c in result] == [1, 2]
    assert result[0].section_path == result[1].section_path == "4.1"


def test_synthetic_policy_has_every_clause():
    result = build_chunks(lines_from_text(POLICY_PATH.read_text(encoding="utf-8")), "pol")
    clause_ids = [c.section_path.split(" > ")[-1] for c in result if " > " in c.section_path]
    assert clause_ids == ["3.1", "3.2", "4.1", "4.2", "4.3", "5.1", "5.2", "5.3", "5.4",
                          "6.1", "6.2", "7.1", "7.2", "7.3"]
