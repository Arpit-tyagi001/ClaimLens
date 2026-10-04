from types import SimpleNamespace

from contracts.schemas import Chunk

from ai.claimlens_ai.retrieval import (
    build_index,
    get_retriever,
)

from ai.claimlens_ai.tools import ClaimLensTools

from ai.claimlens_ai.investigator import run_investigation

from ai.claimlens_ai.verifier import run_verification


def main():

    # =========================================================
    # 1. CREATE SAMPLE POLICY CHUNKS
    # =========================================================

    chunks = [
        Chunk(
            chunk_id="chunk-001",
            doc_id="doc-001",
            section_path="Waiting Period",
            page=3,
            text="A waiting period of 30 days applies to illness claims.",
            bbox=[0.0, 0.0, 100.0, 100.0],
            tags=[],
        ),
        Chunk(
            chunk_id="chunk-002",
            doc_id="policy-001",
            section_path="Exclusions",
            page=7,
            text="Pre-existing diseases are excluded from coverage.",
            bbox=[0.0, 0.0, 100.0, 100.0],
            tags=[],
        ),
    ]

    # =========================================================
    # 2. BUILD RETRIEVAL INDEX
    # =========================================================

    build_index(
        case_id="case-001",
        chunks=chunks,
    )

    # =========================================================
    # 3. CREATE TOOLS
    # =========================================================

    retriever = get_retriever("case-001")

    tools = ClaimLensTools(retriever)

    # =========================================================
    # 4. CREATE REJECTION
    # =========================================================

    rejection = SimpleNamespace(
        reason_id="reason-001",
        text="Claim rejected because waiting period applies.",
    )

    # =========================================================
    # 5. RUN INVESTIGATOR
    # =========================================================

    findings = run_investigation(
        case_id="case-001",
        rejection=rejection,
        facts={},
        emit=lambda event: print("EVENT:", event),
    )

    print()
    print("INVESTIGATOR OUTPUT")
    print("=" * 60)

    for finding in findings:
        print(finding.model_dump_json(indent=2))

    # =========================================================
    # 6. NORMAL VERIFICATION
    # =========================================================

    verified = run_verification(
        case_id="case-001",
        findings=findings,
        tools=tools,
        facts={},
        emit=lambda event: print("EVENT:", event),
        inject_fake_citation=False,
    )

    print()
    print("NORMAL VERIFICATION")
    print("=" * 60)

    for result in verified:
        print(result.model_dump_json(indent=2))

    # ---------------------------------------------------------
    # Assertions
    # ---------------------------------------------------------

    assert len(verified) == 1

    normal_result = verified[0]

    assert normal_result.status == "VERIFIED"

    assert normal_result.final_confidence == normal_result.confidence

    # M1's shared schema stores citation_checks as List[Any],
    # therefore the values are dictionaries after validation.
    assert all(
        check.grounded
        for check in normal_result.citation_checks
    )

    print()
    print("NORMAL VERIFICATION PASSED")

    # =========================================================
    # 7. FAKE CITATION ATTACK
    # =========================================================

    print()
    print("FAKE CITATION ATTACK")
    print("=" * 60)

    attacked = run_verification(
        case_id="case-001",
        findings=findings,
        tools=tools,
        facts={},
        emit=lambda event: print("EVENT:", event),
        inject_fake_citation=True,
    )

    # =========================================================
    # 8. DISPLAY ATTACK RESULT
    # =========================================================

    for result in attacked:
        print(result.model_dump_json(indent=2))

    # ---------------------------------------------------------
    # Assertions
    # ---------------------------------------------------------

    assert len(attacked) == 1

    attacked_result = attacked[0]

    assert attacked_result.status == "REJECTED_UNGROUNDED"

    assert attacked_result.final_confidence == 0.0

    assert any(
        not check.grounded
        for check in attacked_result.citation_checks
    )

    print()
    print("FAKE CITATION ATTACK PASSED")

    print()
    print("========================================")
    print("VERIFIER TEST PASSED")
    print("========================================")


if __name__ == "__main__":
    main()