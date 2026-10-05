from contracts.schemas import Chunk
from ai.claimlens_ai.retrieval import build_index
from ai.claimlens_ai.investigator import run_investigation
from ai.claimlens_ai.verifier import run_verification
from ai.claimlens_ai.drafter import draft_review_request


def emit(stage, detail):
    print(f"EVENT: {stage}", detail)


def main():

    case_id = "integration-case-001"

    # =========================================================
    # 1. POLICY DATA
    # =========================================================

    chunks = [
        Chunk(
            chunk_id="chunk-001",
            doc_id="policy-001",
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
    # 2. BUILD INDEX
    # =========================================================

    print()
    print("=== 1. BUILD INDEX ===")

    build_index(
        case_id=case_id,
        chunks=chunks,
    )

    print("Index built successfully")

    # =========================================================
    # 3. REJECTION
    # =========================================================

    rejection = type(
        "Rejection",
        (),
        {
            "reason_id": "reason-001",
            "text": "Claim rejected because waiting period applies.",
        },
    )()

    # =========================================================
    # 4. INVESTIGATOR
    # =========================================================

    print()
    print("=== 2. INVESTIGATOR ===")

    findings = run_investigation(
        case_id=case_id,
        rejection=rejection,
        facts={},
        emit=emit,
    )

    print(f"Findings: {len(findings)}")

    assert len(findings) == 1

    finding = findings[0]

    print(
        f"{finding.finding_id} | "
        f"{finding.assessment} | "
        f"{finding.confidence}"
    )

    assert finding.assessment == "SUPPORTED"
    assert finding.confidence == 0.75
    assert len(finding.evidence) > 0

    # =========================================================
    # 5. VERIFIER
    # =========================================================

    print()
    print("=== 3. VERIFIER ===")

    verified = run_verification(
        case_id=case_id,
        findings=findings,
        emit=emit,
        inject_fake_citation=False,
    )

    print(f"Verified findings: {len(verified)}")

    assert len(verified) == 1

    verified_finding = verified[0]

    print(
        f"{verified_finding.finding_id} | "
        f"{verified_finding.status} | "
        f"{verified_finding.final_assessment} | "
        f"{verified_finding.final_confidence}"
    )

    assert verified_finding.status == "VERIFIED"
    assert verified_finding.final_confidence <= finding.confidence
    assert len(verified_finding.citation_checks) > 0
    assert all(
        check.grounded
        for check in verified_finding.citation_checks
    )

    assert len(verified_finding.challenges) == 1

    challenge = verified_finding.challenges[0]

    assert challenge.round == 1
    assert challenge.rebuttal is not None

    # =========================================================
    # 6. DRAFTER
    # =========================================================

    print()
    print("=== 4. DRAFTER ===")

    draft = draft_review_request(
        case_id=case_id,
        approved=[verified_finding],
    )

    print("DRAFT:")
    print(draft.model_dump())

    assert draft.case_id == case_id
    assert len(draft.finding_ids) == 1
    assert finding.finding_id in draft.finding_ids
    assert draft.text
    assert "[chunk-001]" in draft.text

    # =========================================================
    # 7. FINAL RESULT
    # =========================================================

    print()
    print("================================")
    print("M3 INTEGRATION TEST PASSED")
    print("================================")


if __name__ == "__main__":
    main()