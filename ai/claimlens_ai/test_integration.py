from ai.claimlens_ai.retrieval import Chunk, build_index, get_retriever
from ai.claimlens_ai.tools import ClaimLensTools
from ai.claimlens_ai.investigator import run_investigation
from ai.claimlens_ai.verifier import run_verification
from ai.claimlens_ai.drafter import draft_review_request

from contracts.schemas import RejectionReason


def emit(event):
    print("EVENT:", event)


def main():

    case_id = "integration-case-001"

    chunks = [
        Chunk(
            chunk_id="chunk-001",
            doc_id="policy-001",
            section_path="Waiting Period",
            page=3,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="A waiting period of 30 days applies to illness claims.",
        ),
        Chunk(
            chunk_id="chunk-002",
            doc_id="policy-001",
            section_path="Exclusions",
            page=7,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="Pre-existing diseases are excluded from coverage.",
        ),
        Chunk(
            chunk_id="chunk-003",
            doc_id="policy-001",
            section_path="Documents",
            page=10,
            bbox=[0.0, 0.0, 100.0, 100.0],
            text="The claimant must submit the required hospital documents.",
        ),
    ]

    print("\n=== 1. BUILD INDEX ===")
    build_index(case_id, chunks)
    print("Index built successfully")
    retriever = get_retriever(case_id)
    tools = ClaimLensTools(retriever)

    rejection = RejectionReason(
        reason_id="reason-001",
        text="Claim rejected because the illness occurred within the waiting period.",
        category="waiting_period",
        page=1,
        bbox=[0.0, 0.0, 100.0, 100.0],
    )

    facts = {
        "admission_date": "2026-01-10",
        "policy_start_date": "2025-12-20",
        "claim_type": "illness",
    }

    print("\n=== 2. INVESTIGATOR ===")

    findings = run_investigation(
        case_id=case_id,
        rejection=rejection,
        facts=facts,
        emit=emit,
    )

    print("Findings:", len(findings))

    for finding in findings:
        print(
            finding.finding_id,
            "|",
            finding.assessment,
            "|",
            finding.confidence,
        )

    if not findings:
        raise RuntimeError("No findings generated")

    print("\n=== 3. VERIFIER ===")

    verified = run_verification(
        case_id=case_id,
        findings=findings,
        tools=tools,
        emit=emit,
    )

    print("Verified findings:", len(verified))

    for finding in verified:
        print(
            finding.finding_id,
            "|",
            finding.status,
            "|",
            finding.final_assessment,
            "|",
            finding.final_confidence,
        )

    print("\n=== 4. DRAFTER ===")

    approved = [
        finding
        for finding in verified
        if finding.status == "VERIFIED"
    ]

    if not approved:
        print("No VERIFIED findings. Draft not created.")
        return

    draft = draft_review_request(
        case_id=case_id,
        approved=approved,
    )

    print("\nDRAFT:")
    print(draft)

    print("\n================================")
    print("M3 INTEGRATION TEST PASSED")
    print("================================")


if __name__ == "__main__":
    main()
