from ai.claimlens_ai.retrieval import (
    Chunk,
    build_index,
)


def main():

    chunks = [
        Chunk(
            chunk_id="chunk-001",
            doc_id="policy-001",
            section_path="Waiting Period",
            page=3,
            text="A waiting period of 30 days applies to illness claims.",
        ),
        Chunk(
            chunk_id="chunk-002",
            doc_id="policy-001",
            section_path="Exclusions",
            page=7,
            text="Pre-existing diseases are excluded from coverage.",
        ),
        Chunk(
            chunk_id="chunk-003",
            doc_id="policy-001",
            section_path="Documents",
            page=10,
            text="The claimant must submit the required hospital documents.",
        ),
    ]

    retriever = build_index(
        case_id="case-001",
        chunks=chunks,
    )

    results = retriever.search(
        "waiting period illness",
        top_k=2,
    )

    print("RETRIEVAL TEST PASSED")

    for result in results:
        print(
            result.chunk_id,
            "|",
            result.section_path,
            "|",
            result.fts_rank,
        )


if __name__ == "__main__":
    main()