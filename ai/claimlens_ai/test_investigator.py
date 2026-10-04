from types import SimpleNamespace

from contracts.schemas import Chunk

from ai.claimlens_ai.retrieval import build_index

from ai.claimlens_ai.investigator import (
    run_investigation,
)


def main():

    # ---------------------------------------------------------
    # 1. Create sample policy chunks
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # 2. Build the retrieval index
    # ---------------------------------------------------------

    build_index(
        case_id="case-001",
        chunks=chunks,
    )

    # ---------------------------------------------------------
    # 3. Create a rejection reason
    # ---------------------------------------------------------

    rejection = SimpleNamespace(
        reason_id="reason-001",
        text="Claim rejected because waiting period applies.",
    )

    # ---------------------------------------------------------
    # 4. Run investigator
    # ---------------------------------------------------------

    findings = run_investigation(
        case_id="case-001",
        rejection=rejection,
        facts={},
        emit=lambda event: print("EVENT:", event),
    )

    # ---------------------------------------------------------
    # 5. Display results
    # ---------------------------------------------------------

    print()
    print("INVESTIGATOR TEST PASSED")
    print()

    for finding in findings:
        print("Finding:")
        print(finding.model_dump_json(indent=2))


if __name__ == "__main__":
    main()