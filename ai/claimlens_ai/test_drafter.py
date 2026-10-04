from contracts.schemas import (
    Evidence,
    VerifiedFinding,
)
from ai.claimlens_ai.drafter import draft_review_request


def main():
    finding = VerifiedFinding(
        finding_id="case-001-reason-001",
        reason_id="reason-001",
        assessment="SUPPORTED",
        confidence=0.75,
        reasoning="The cited policy evidence supports the rejection ground.",
        evidence=[
            Evidence(
                chunk_id="chunk-001",
                quote="A waiting period of 30 days applies to illness claims.",
                page=3,
                section_path="Waiting Period",
            )
        ],
        facts_used=[],
        rule_checks=[],
        citation_checks=[],
        fact_checks=[],
        challenges=[],
        status="VERIFIED",
        final_assessment="SUPPORTED",
        final_confidence=0.75,
    )

    draft = draft_review_request(
        case_id="case-001",
        approved=[finding],
    )

    print()
    print("DRAFT OUTPUT")
    print("=" * 60)

    if hasattr(draft, "model_dump_json"):
        print(draft.model_dump_json(indent=2))
    else:
        print(draft)
        # Verify that non-approved findings are rejected.
    downgraded = finding.model_copy(
        update={"status": "DOWNGRADED"}
    )

    try:
        draft_review_request(
            case_id="case-001",
            approved=[downgraded],
        )
        raise AssertionError("Downgraded finding should not be accepted")
    except ValueError:
        print("NEGATIVE TEST PASSED")
    print()
    print("DRAFTER TEST PASSED")


if __name__ == "__main__":
    main()